"""HackSphere 3.0 - Smart Energy Microgrid web app (Catalyst AppSail entry point).

    python3 -u app.py

Standard library only: nothing to pip-install on AppSail.
Listens on X_ZOHO_CATALYST_LISTEN_PORT (set by Catalyst), default 9000 for local runs.
"""
import json
import mimetypes
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import engine
import predict as predictor

ROOT = os.path.dirname(os.path.abspath(__file__))
# Normal layout: static/ and data/ sub-folders.  Flat layout (every file in one folder, which is what you get when
# files are uploaded to GitHub without their folders) is also supported.
STATIC = os.path.join(ROOT, "static")
if not os.path.isdir(STATIC):
    STATIC = ROOT
SAMPLE_CSV = next((p for p in (os.path.join(ROOT, "data", "sample_test_input.csv"),
                               os.path.join(ROOT, "sample_test_input.csv")) if os.path.isfile(p)),
                  os.path.join(ROOT, "data", "sample_test_input.csv"))
STATIC_EXT = {".html", ".js", ".css", ".woff2", ".png", ".svg", ".ico"}   # never serve .py / .json / .csv files
MAX_BODY = 2 * 1024 * 1024

try:
    DEMOS = engine.demo_windows(SAMPLE_CSV)
except OSError:
    DEMOS = []

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/javascript", ".js")


def config_payload():
    return {
        "rules": {
            "storage_demand_kw": engine.STORAGE_DEMAND_KW,
            "overload_kw": engine.OVERLOAD_KW,
            "peak_start_hour": engine.PEAK_START_HOUR,
            "peak_end_hour": engine.PEAK_END_HOUR,
        },
        "battery_defaults": engine.DEFAULT_BATTERY,
        "max_rows": engine.MAX_ROWS,
        "features": predictor.FEATURES,
        "quickml": predictor.quickml_status(),
        "local_model": predictor.local_model_info(),
        "demos": [{"label": d["label"], "rows": len(d["rows"])} for d in DEMOS],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "HackSphereMicrogrid/1.0"

    # ---- helpers
    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _error(self, code, message):
        self._send(code, {"ok": False, "error": message})

    def log_message(self, fmt, *args):  # concise access log to stdout
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    # ---- routes
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._send(200, {"ok": True, "quickml": predictor.quickml_status(),
                                    "local_model": predictor.local_model_info()})
        if path == "/api/config":
            return self._send(200, config_payload())
        if path == "/api/demo":
            q = dict(p.split("=", 1) for p in (urlparse(self.path).query or "").split("&") if "=" in p)
            try:
                idx = int(q.get("i", "0"))
                return self._send(200, {"ok": True, "label": DEMOS[idx]["label"], "rows": DEMOS[idx]["rows"]})
            except (ValueError, IndexError):
                return self._error(404, "Unknown demo window.")
        if path == "/api/logs":
            return self._send(200, {"ok": True, "runs": engine.LOG.all()})
        if path == "/api/template.csv":
            rows = DEMOS[0]["rows"] if DEMOS else []
            lines = ["timestamp,ambient_temp_c,humidity_pct,solar_irradiance_wm2"]
            lines += ["%s,%s,%s,%s" % (r["timestamp"], r["ambient_temp_c"], r["humidity_pct"],
                                        r["solar_irradiance_wm2"]) for r in rows]
            return self._send(200, "\n".join(lines).encode(), "text/csv",
                              {"Content-Disposition": 'attachment; filename="telemetry_template.csv"'})
        return self._static(path)

    do_HEAD = do_GET

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/forecast":
            return self._error(404, "Not found.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(400, "Bad Content-Length.")
        if length <= 0 or length > MAX_BODY:
            return self._error(413, "Request body must be between 1 byte and 2 MB.")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._error(400, "Request body must be valid JSON.")
        try:
            if isinstance(body.get("csv"), str):
                rows = engine.parse_csv_text(body["csv"])
                origin = "csv-upload"
            else:
                rows = body.get("rows")
                origin = body.get("origin") or "form"
            return self._send(200, engine.forecast(rows, body.get("battery"), origin=str(origin)[:24]))
        except engine.ValidationError as exc:
            return self._error(422, str(exc))
        except predictor.QuickMLError as exc:
            return self._error(502, "QuickML request failed: %s" % exc)
        except Exception:  # never leak a stack trace to the browser
            traceback.print_exc()
            return self._error(500, "Unexpected server error. Check the app logs.")

    # ---- static files
    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, rel))
        if (not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full)
                or os.path.splitext(full)[1].lower() not in STATIC_EXT):
            return self._error(404, "Not found.")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            data = fh.read()
        cache = "no-cache" if rel.endswith(".html") else "public, max-age=86400"
        return self._send(200, data, ctype, {"Cache-Control": cache})


def main():
    port = int(os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT", os.environ.get("PORT", "9000")))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    st = predictor.quickml_status()
    print("Microgrid app on :%d | QuickML configured: %s | fallback allowed: %s"
          % (port, st["configured"], st["fallback_allowed"]))
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
