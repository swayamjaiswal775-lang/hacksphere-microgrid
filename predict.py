"""HackSphere 3.0 - predict.py

Demand forecasting client for the Smart Energy Microgrid engine.

    python predict.py --input sample_test_input.csv --output predictions.csv

Contract (matches the starter kit and test_eval.py)
    predict(input_df) -> numpy array, exactly one prediction (kW) per input row.

How a prediction is produced
    1. Every row is turned into the feature vector in FEATURES (see build_features).
       The SAME function builds quickml_train.csv, so training == inference.
    2. The vector is sent to the published Zoho Catalyst QuickML endpoint.
    3. If the endpoint is not configured or the call fails, a small local
       gradient-boosted model (model_fallback.json) answers instead and a warning is
       printed.  Set QUICKML_FALLBACK=0 to make failures loud instead (recommended
       once your endpoint is live, so a mis-configuration can never go unnoticed).

Configuration (environment variables - never commit secrets)
    QUICKML_ENDPOINT_URL   Deployment URL of the published endpoint (not secret)
    QUICKML_ENDPOINT_KEY   Sent as X-QUICKML-ENDPOINT-KEY
    CATALYST_ORG_ID        Sent as CATALYST-ORG
    CATALYST_ENVIRONMENT   Development (default) or Production
    Auth - either a short-lived token ...
    QUICKML_ACCESS_TOKEN   Zoho OAuth access token (scope QuickML.deployment.READ)
    ... or a refresh-token flow (preferred; tokens renew automatically)
    ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN
    ZOHO_ACCOUNTS_URL      default https://accounts.zoho.in  (use .com / .eu / ... for your data centre)
    Optional
    QUICKML_MODE           auto (default) | batch | single
    QUICKML_TIMEOUT        seconds per HTTP call (default 30)
    QUICKML_FALLBACK       1 (default) allow local fallback model, 0 raise instead

This module uses only the Python standard library at import time so the web app
can run on AppSail with zero dependencies.  numpy is imported lazily inside
predict() purely to honour the starter-kit return type.
"""
import argparse
import csv
import json
import os
import re
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Paste your published endpoint URL here, or set QUICKML_ENDPOINT_URL in the environment.
QUICKML_ENDPOINT_URL = os.environ.get(
    "QUICKML_ENDPOINT_URL", "YOUR_PUBLISHED_QUICKML_ENDPOINT_URL_HERE"
)

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "target_demand_kw"

# Feature vector sent to QuickML. Column names/order must match quickml_train.csv.
FEATURES = [
    "hour",
    "ambient_temp_c",
    "humidity_pct",
    "solar_irradiance_wm2",
    "cooling_degree",
    "is_peak_window",
]

# Training-set medians, used only to repair a missing value when no better estimate exists.
DEFAULTS = {"ambient_temp_c": 25.0, "humidity_pct": 60.0, "solar_irradiance_wm2": 0.0}

_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d",
)


# --------------------------------------------------------------------------- features
def parse_timestamp(value):
    """Return a datetime or None. Accepts datetime/pandas Timestamp and common string formats."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "nat", "none", "null"):
        return None
    s = re.sub(r"(Z|[+-]\d\d:?\d\d)$", "", s)  # drop timezone suffix
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def to_float(value):
    """float(value) or None for blank / NaN / non-numeric."""
    if value is None:
        return None
    try:
        f = float(str(value).strip().replace(",", ""))
    except ValueError:
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def build_features(row):
    """Feature vector for ONE row. Row-independent on purpose: no lag/rolling terms,
    so any batch size (1 row or 1000) gives identical features."""
    ts = parse_timestamp(row.get("timestamp"))
    hour = ts.hour if ts else 0
    temp = to_float(row.get("ambient_temp_c"))
    hum = to_float(row.get("humidity_pct"))
    sol = to_float(row.get("solar_irradiance_wm2"))
    temp = DEFAULTS["ambient_temp_c"] if temp is None else temp
    hum = DEFAULTS["humidity_pct"] if hum is None else hum
    sol = DEFAULTS["solar_irradiance_wm2"] if sol is None else sol
    return {
        "hour": hour,
        "ambient_temp_c": temp,
        "humidity_pct": hum,
        "solar_irradiance_wm2": sol,
        "cooling_degree": round(max(0.0, temp - 18.0), 4),
        "is_peak_window": 1 if 18 <= hour <= 22 else 0,
    }


# --------------------------------------------------------------------------- local fallback
class LocalModel:
    """Gradient-boosted trees exported from scikit-learn, evaluated in pure Python."""

    def __init__(self, path=None):
        path = path or os.path.join(HERE, "model_fallback.json")
        self.spec = None
        try:
            with open(path) as fh:
                self.spec = json.load(fh)
        except (OSError, ValueError):
            self.spec = None

    @property
    def available(self):
        return self.spec is not None

    @property
    def cv_rmse(self):
        return (self.spec or {}).get("cv_rmse_mean")

    def predict_one(self, feats):
        if not self.spec:  # last-resort heuristic, only if model_fallback.json is missing
            hr = feats["hour"]
            base = 92.0 if (hr < 6 or hr == 23) else (215.0 if 18 <= hr <= 22 else 165.0)
            return base + max(0.0, feats["ambient_temp_c"] - 24.0) * 2.0
        # scikit-learn compares float32 values against thresholds; mirror that for exact parity
        x = [struct.unpack("f", struct.pack("f", float(feats[name])))[0] for name in self.spec["features"]]
        total = self.spec["init"]
        lr = self.spec["lr"]
        for t in self.spec["trees"]:
            node = 0
            left, right, fidx, thr = t["l"], t["r"], t["f"], t["t"]
            while left[node] != -1:
                node = left[node] if x[fidx[node]] <= thr[node] else right[node]
            total += lr * t["v"][node]
        return total


_LOCAL = LocalModel()


# --------------------------------------------------------------------------- QuickML client
class QuickMLError(RuntimeError):
    pass


def _is_configured():
    url = os.environ.get("QUICKML_ENDPOINT_URL", QUICKML_ENDPOINT_URL)
    return bool(url) and not url.startswith("YOUR_PUBLISHED")


def _fallback_allowed():
    return os.environ.get("QUICKML_FALLBACK", "1").strip().lower() not in ("0", "false", "no")


def _extract_numbers(obj, depth=0):
    """Find the list of numeric predictions inside the many shapes an endpoint can return.
    Returns a list of floats or None."""
    if depth > 6 or obj is None:
        return None
    f = None if isinstance(obj, (dict, list, tuple)) else to_float(obj)
    if f is not None:
        return [f]
    if isinstance(obj, dict):
        for key in ("predictions", "prediction", "result", "results", "output", "outputs",
                    "predicted", "data", "response", TARGET):
            if key in obj:
                got = _extract_numbers(obj[key], depth + 1)
                if got is not None:
                    return got
        nums = [v for v in obj.values() if not isinstance(v, (dict, list)) and to_float(v) is not None]
        if len(nums) == 1:
            return [to_float(nums[0])]
        return None
    if isinstance(obj, (list, tuple)):
        out = []
        for item in obj:
            got = _extract_numbers(item, depth + 1)
            if got is None or len(got) != 1:
                return None
            out.append(got[0])
        return out
    return None


class QuickMLClient:
    def __init__(self):
        self._lock = threading.Lock()
        self._token = None
        self._token_exp = 0.0
        self.mode = None  # resolved on first call when QUICKML_MODE=auto

    # -- config helpers (read env at call time so the app can be reconfigured without restart)
    @property
    def url(self):
        return os.environ.get("QUICKML_ENDPOINT_URL", QUICKML_ENDPOINT_URL)

    @property
    def timeout(self):
        return float(os.environ.get("QUICKML_TIMEOUT", "30"))

    def _access_token(self, force=False):
        static = os.environ.get("QUICKML_ACCESS_TOKEN")
        cid, secret, refresh = (os.environ.get("ZOHO_CLIENT_ID"), os.environ.get("ZOHO_CLIENT_SECRET"),
                                os.environ.get("ZOHO_REFRESH_TOKEN"))
        if not (cid and secret and refresh):
            if static:
                return static
            raise QuickMLError("No credentials: set QUICKML_ACCESS_TOKEN or "
                               "ZOHO_CLIENT_ID/ZOHO_CLIENT_SECRET/ZOHO_REFRESH_TOKEN")
        with self._lock:
            if not force and self._token and time.time() < self._token_exp:
                return self._token
            accounts = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in").rstrip("/")
            body = urllib.parse.urlencode({"refresh_token": refresh, "client_id": cid,
                                           "client_secret": secret, "grant_type": "refresh_token"}).encode()
            try:
                with urllib.request.urlopen(urllib.request.Request(accounts + "/oauth/v2/token", data=body,
                                                                   method="POST"), timeout=self.timeout) as r:
                    payload = json.loads(r.read().decode())
            except (urllib.error.URLError, ValueError) as exc:
                raise QuickMLError("OAuth token refresh failed: %s" % exc)
            if "access_token" not in payload:
                raise QuickMLError("OAuth token refresh rejected: %s" % payload.get("error", payload))
            self._token = payload["access_token"]
            self._token_exp = time.time() + max(60, int(payload.get("expires_in", 3600)) - 300)
            return self._token

    def _post(self, body, retry_auth=True):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Zoho-oauthtoken " + self._access_token(),
        }
        if os.environ.get("QUICKML_ENDPOINT_KEY"):
            headers["X-QUICKML-ENDPOINT-KEY"] = os.environ["QUICKML_ENDPOINT_KEY"]
        if os.environ.get("CATALYST_ORG_ID"):
            headers["CATALYST-ORG"] = os.environ["CATALYST_ORG_ID"]
        headers["Environment"] = os.environ.get("CATALYST_ENVIRONMENT", "Development")
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers, method="POST")
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode() or "null")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:300]
                if exc.code == 401 and retry_auth:
                    self._access_token(force=True)
                    return self._post(body, retry_auth=False)
                if exc.code in (429, 500, 502, 503, 504):
                    last = QuickMLError("HTTP %s %s" % (exc.code, detail))
                    time.sleep(0.6 * (attempt + 1))
                    continue
                raise QuickMLError("HTTP %s %s" % (exc.code, detail))
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last = QuickMLError("request failed: %s" % exc)
                time.sleep(0.6 * (attempt + 1))
        raise last or QuickMLError("request failed")

    def _predict_batch(self, feats):
        got = _extract_numbers(self._post({"data": feats}))
        if got is None or len(got) != len(feats):
            raise QuickMLError("batch response did not contain %d predictions" % len(feats))
        return got

    def _predict_single(self, feats):
        def one(f):
            got = _extract_numbers(self._post(f))
            if got is None or len(got) != 1:
                raise QuickMLError("single-row response did not contain exactly one prediction")
            return got[0]

        if len(feats) == 1:
            return [one(feats[0])]
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(one, feats))

    def predict(self, feats):
        mode = (os.environ.get("QUICKML_MODE", "auto") or "auto").lower()
        if mode == "auto" and self.mode:
            mode = self.mode
        if mode == "batch":
            return self._predict_batch(feats)
        if mode == "single":
            return self._predict_single(feats)
        # auto: probe the organiser-style batch contract first, then flat one-row-per-call
        try:
            out = self._predict_batch(feats)
            self.mode = "batch"
            return out
        except QuickMLError as batch_err:
            try:
                out = self._predict_single(feats)
                self.mode = "single"
                return out
            except QuickMLError as single_err:
                raise QuickMLError("batch mode: %s | single mode: %s" % (batch_err, single_err))


_CLIENT = QuickMLClient()


# --------------------------------------------------------------------------- public API
def forecast_rows(rows):
    """Predict demand for a list of row dicts.

    Returns {"predictions": [...], "source": "quickml"|"local_fallback", "detail": str}.
    """
    feats = [build_features(r) for r in rows]
    if not feats:
        return {"predictions": [], "source": "none", "detail": "no rows"}
    detail = ""
    if _is_configured():
        try:
            preds = _CLIENT.predict(feats)
            return {"predictions": preds, "source": "quickml",
                    "detail": "QuickML endpoint (%s mode)" % (_CLIENT.mode or "configured")}
        except QuickMLError as exc:
            detail = "QuickML call failed: %s" % exc
            if not _fallback_allowed():
                raise
    else:
        detail = "QuickML endpoint not configured"
        if not _fallback_allowed():
            raise QuickMLError(detail)
    print("Warning: %s - using local fallback model." % detail, file=sys.stderr)
    return {"predictions": [_LOCAL.predict_one(f) for f in feats], "source": "local_fallback", "detail": detail}


def predict(input_df):
    """Starter-kit entry point.

    Parameters
        input_df: DataFrame with ['timestamp','ambient_temp_c','humidity_pct','solar_irradiance_wm2']
    Returns
        numpy.ndarray of predicted demand (kW), one value per input row, same order.
    """
    import numpy as np  # lazy: the web app never needs numpy

    rows = input_df.to_dict(orient="records")
    result = forecast_rows(rows)
    return np.array(result["predictions"], dtype=float)


def local_model_info():
    return {"available": _LOCAL.available, "cv_rmse_kw": _LOCAL.cv_rmse}


def quickml_status():
    return {"configured": _is_configured(), "mode": _CLIENT.mode, "fallback_allowed": _fallback_allowed()}


def _main():
    ap = argparse.ArgumentParser(description="Predict hourly demand (kW) via QuickML")
    ap.add_argument("--input", default="sample_test_input.csv")
    ap.add_argument("--output", default="predictions.csv")
    args = ap.parse_args()
    with open(args.input, newline="") as fh:
        rows = list(csv.DictReader(fh))
    result = forecast_rows(rows)
    with open(args.output, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["timestamp", TARGET])
        for r, p in zip(rows, result["predictions"]):
            w.writerow([r["timestamp"], "%.2f" % p])
    print("Wrote %d predictions to %s (source: %s)" % (len(rows), args.output, result["source"]))


if __name__ == "__main__":
    _main()
