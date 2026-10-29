"""Microgrid engine: ingestion/cleanup -> forecast -> transition controller -> battery simulation.

Pure standard library (runs on AppSail with no pip installs).
"""
import csv
import io
import json
import os
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timedelta

import predict as predictor
from predict import parse_timestamp, to_float

# ----------------------------------------------------------------------------- spec constants
STORAGE_DEMAND_KW = 160.0   # demand strictly above this -> Renewable Storage Mode
OVERLOAD_KW = 200.0         # demand at/above this -> Critical Overload Alert
PEAK_START_HOUR = 18        # 18:00 ..
PEAK_END_HOUR = 22          # .. 22:00 inclusive (hour 22 counts, hour 23 does not)

MODE_STORAGE = "Renewable Storage Mode"
MODE_GRID = "Main Grid Mode"

MAX_ROWS = 168              # one week of hourly rows; the headline use case is 24

COLUMN_ALIASES = {
    "timestamp": ("timestamp", "time", "datetime", "date_time", "date", "ts"),
    "ambient_temp_c": ("ambient_temp_c", "temperature", "temp", "temp_c", "ambient_temp", "ambient_temperature"),
    "humidity_pct": ("humidity_pct", "humidity", "rh", "relative_humidity"),
    "solar_irradiance_wm2": ("solar_irradiance_wm2", "solar_irradiance", "irradiance", "solar", "ghi"),
}

# physical plausibility limits (outside -> treated as a bad reading and repaired)
LIMITS = {"ambient_temp_c": (-60.0, 65.0), "humidity_pct": (0.0, 100.0), "solar_irradiance_wm2": (0.0, 1500.0)}

DEFAULT_BATTERY = {
    "capacity_kwh": 4000.0,
    "initial_soc_pct": 90.0,
    "max_discharge_kw": 300.0,
    "max_charge_kw": 200.0,
    "reserve_pct": 10.0,
}


class ValidationError(ValueError):
    pass


# ----------------------------------------------------------------------------- ingestion & cleanup
def parse_csv_text(text):
    """CSV text -> list of dict rows with canonical column names."""
    text = (text or "").lstrip("\ufeff").strip()
    if not text:
        raise ValidationError("The CSV is empty.")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValidationError("The CSV has no header row.")
    return [dict(r) for r in reader]


def _canonical_keys(rows):
    """Map whatever headers the user supplied onto the four canonical columns."""
    keys = {str(k).strip().lower(): k for k in rows[0].keys() if k is not None}
    mapping = {}
    for canon, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in keys:
                mapping[canon] = keys[a]
                break
    if "timestamp" not in mapping:
        raise ValidationError("Missing required column: timestamp (YYYY-MM-DD HH:MM:SS).")
    absent_weather = [c for c in LIMITS if c not in mapping]
    out = []
    for r in rows:
        out.append({c: r.get(mapping[c]) if c in mapping else None for c in COLUMN_ALIASES})
    return out, absent_weather, ("target_demand_kw" in keys)


def clean_rows(raw_rows):
    """Validate, repair and order raw telemetry. Returns (rows, report).

    report = {"steps": [human-readable repairs], "warnings": [...], "rows_in": n, "rows_out": n}
    Every repair is counted so the dashboard can show exactly what was changed.
    """
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValidationError("No telemetry rows were provided.")
    mapped, absent_weather, has_target = _canonical_keys(raw_rows)
    steps, warnings = [], []
    rows_in = len(mapped)
    if has_target:
        steps.append("Ignored the target_demand_kw column (inputs must not contain the answer).")

    # 1. drop empty rows, parse timestamps
    parsed = []
    bad_ts = 0
    for r in mapped:
        if all((v is None or str(v).strip() == "") for v in r.values()):
            continue
        ts = parse_timestamp(r["timestamp"])
        if ts is None:
            bad_ts += 1
            continue
        parsed.append((ts, r))
    if bad_ts:
        steps.append("Dropped %d row(s) with a missing or unreadable timestamp." % bad_ts)
    if not parsed:
        raise ValidationError("No row had a readable timestamp. Use the format YYYY-MM-DD HH:MM:SS.")

    # 2. de-duplicate (keep the last reading for a repeated hour) and sort into time order
    was_sorted = all(parsed[i][0] <= parsed[i + 1][0] for i in range(len(parsed) - 1))
    seen = {}
    for ts, r in parsed:
        seen[ts] = r
    dups = len(parsed) - len(seen)
    if dups:
        steps.append("Removed %d duplicate timestamp(s), keeping the latest reading." % dups)
    ordered = sorted(seen.items(), key=lambda p: p[0])
    if not was_sorted:
        steps.append("Sorted rows into time order.")

    # 3. truncate to MAX_ROWS
    if len(ordered) > MAX_ROWS:
        warnings.append("Received %d rows; using the first %d (%d hours)." % (len(ordered), MAX_ROWS, MAX_ROWS))
        ordered = ordered[:MAX_ROWS]

    # 4. numeric coercion + plausibility
    cols = list(LIMITS)
    values = {c: [] for c in cols}
    for c in cols:
        lo, hi = LIMITS[c]
        n_blank = n_bad = n_clip = 0
        for ts, r in ordered:
            raw = r.get(c)
            v = to_float(raw)
            if v is None:
                n_blank += 1
                values[c].append(None)
                continue
            if c == "humidity_pct" and hi < v <= hi + 5:
                v, n_clip = hi, n_clip + 1
            elif c == "solar_irradiance_wm2" and lo - 50 <= v < lo:
                v, n_clip = lo, n_clip + 1
            if not (lo <= v <= hi):
                n_bad += 1
                values[c].append(None)
                continue
            values[c].append(v)
        label = c
        if c in absent_weather:
            warnings.append("Column %s was not provided; filled with a typical training value." % c)
        elif n_blank:
            steps.append("%s: %d missing/non-numeric value(s) detected." % (label, n_blank))
        if n_bad:
            steps.append("%s: %d out-of-range value(s) (outside %g to %g) treated as missing." % (label, n_bad, lo, hi))
        if n_clip:
            steps.append("%s: %d slightly out-of-range value(s) clipped to the valid range." % (label, n_clip))

    # 5. impute: linear interpolation in time, nearest at the ends, typical value if the column is empty
    for c in cols:
        series = values[c]
        known = [i for i, v in enumerate(series) if v is not None]
        if not known:
            fill = predictor.DEFAULTS[c]
            series[:] = [fill] * len(series)
            if c not in absent_weather:
                warnings.append("No usable %s values; used the typical training value %g." % (c, fill))
            continue
        filled = 0
        for i, v in enumerate(series):
            if v is not None:
                continue
            prev = max([k for k in known if k < i], default=None)
            nxt = min([k for k in known if k > i], default=None)
            if prev is not None and nxt is not None:
                t0, t1, ti = ordered[prev][0], ordered[nxt][0], ordered[i][0]
                span = (t1 - t0).total_seconds() or 1.0
                w = (ti - t0).total_seconds() / span
                series[i] = series[prev] + w * (series[nxt] - series[prev])
            else:
                series[i] = series[prev if prev is not None else nxt]
            filled += 1
        if filled and c not in absent_weather:
            steps.append("%s: filled %d gap(s) by time interpolation." % (c, filled))

    # 6. cadence check (the model is hourly)
    gaps = 0
    for i in range(len(ordered) - 1):
        if ordered[i + 1][0] - ordered[i][0] != timedelta(hours=1):
            gaps += 1
    if gaps:
        warnings.append("%d step(s) are not exactly one hour apart; the forecast is still produced row by row." % gaps)
    off_hour = sum(1 for ts, _ in ordered if ts.minute or ts.second)
    if off_hour:
        warnings.append("%d timestamp(s) are not on the hour; the hour of day is used for the rules." % off_hour)

    rows = []
    for i, (ts, _) in enumerate(ordered):
        rows.append({
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "ambient_temp_c": round(values["ambient_temp_c"][i], 2),
            "humidity_pct": round(values["humidity_pct"][i], 2),
            "solar_irradiance_wm2": round(values["solar_irradiance_wm2"][i], 2),
        })
    if not steps:
        steps.append("Data was already clean: no repairs were needed.")
    return rows, {"steps": steps, "warnings": warnings, "rows_in": rows_in, "rows_out": len(rows)}


# ----------------------------------------------------------------------------- transition controller
def classify(demand_kw, hour):
    """Section 6 of the problem statement, exactly.

    Renewable Storage Mode : demand > 160 kW  OR  hour in 18..22 inclusive
    Main Grid Mode         : otherwise (demand <= 160 kW AND outside the window)
    Critical Overload      : demand >= 200 kW (independent of the mode)
    """
    in_window = PEAK_START_HOUR <= hour <= PEAK_END_HOUR
    over_threshold = demand_kw > STORAGE_DEMAND_KW
    mode = MODE_STORAGE if (over_threshold or in_window) else MODE_GRID
    reasons = []
    if over_threshold:
        reasons.append("Demand %.2f kW is above %g kW" % (demand_kw, STORAGE_DEMAND_KW))
    if in_window:
        reasons.append("%02d:00 is inside the %02d:00-%02d:00 peak window" % (hour, PEAK_START_HOUR, PEAK_END_HOUR))
    if not reasons:
        reasons.append("Demand %.2f kW is at or below %g kW and outside the peak window" % (demand_kw, STORAGE_DEMAND_KW))
    return mode, demand_kw >= OVERLOAD_KW, reasons


def sanitize_battery(b):
    out = dict(DEFAULT_BATTERY)
    for k, v in (b or {}).items():
        f = to_float(v)
        if k in out and f is not None:
            out[k] = f
    out["capacity_kwh"] = min(max(out["capacity_kwh"], 10.0), 100000.0)
    out["initial_soc_pct"] = min(max(out["initial_soc_pct"], 0.0), 100.0)
    out["reserve_pct"] = min(max(out["reserve_pct"], 0.0), 50.0)
    out["max_discharge_kw"] = min(max(out["max_discharge_kw"], 1.0), 10000.0)
    out["max_charge_kw"] = min(max(out["max_charge_kw"], 0.0), 10000.0)
    return out


def run_controller(rows, preds, battery):
    """Apply the rules per hour and simulate the battery (1 h steps).

    The rule outcome (grid mode / overload) is fixed by the spec and never altered by the
    battery model.  The battery model only adds information: state of charge, how much the
    battery supplies, and a warning if the battery cannot cover the demand.
    """
    cap = battery["capacity_kwh"]
    reserve = cap * battery["reserve_pct"] / 100.0
    energy = cap * battery["initial_soc_pct"] / 100.0
    out = []
    for r, p in zip(rows, preds):
        demand = round(float(p), 2)
        ts = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        mode, overload, reasons = classify(demand, ts.hour)
        battery_kw = 0.0
        limited = False
        if mode == MODE_STORAGE:
            available = max(0.0, energy - reserve)
            discharge = min(demand, battery["max_discharge_kw"], available)
            limited = discharge + 1e-9 < demand
            energy -= discharge
            battery_kw = -discharge
            grid_kw = demand - discharge
        else:
            charge = min(battery["max_charge_kw"], max(0.0, cap - energy))
            energy += charge
            battery_kw = charge
            grid_kw = demand + charge
        out.append({
            **r,
            "hour": ts.hour,
            "predicted_demand_kw": demand,
            "grid_mode": mode,
            "overload": overload,
            "reasons": reasons,
            "in_peak_window": PEAK_START_HOUR <= ts.hour <= PEAK_END_HOUR,
            "battery_kw": round(battery_kw, 2),
            "grid_import_kw": round(grid_kw, 2),
            "soc_pct": round(100.0 * energy / cap, 2),
            "battery_limited": limited,
        })
    return out


def build_signals(hours):
    """Power-source transition signals (a signal is emitted whenever the mode changes)."""
    signals, prev = [], None
    for h in hours:
        if h["grid_mode"] != prev:
            to_storage = h["grid_mode"] == MODE_STORAGE
            signals.append({
                "timestamp": h["timestamp"],
                "signal": "SWITCH_TO_RENEWABLE_STORAGE" if to_storage else "SWITCH_TO_MAIN_GRID",
                "from": prev or "Initial state",
                "to": h["grid_mode"],
                "trigger": "; ".join(h["reasons"]),
                "initial": prev is None,
            })
            prev = h["grid_mode"]
    return signals


def build_alerts(hours):
    alerts = []
    for h in hours:
        if h["overload"]:
            alerts.append({"level": "critical", "timestamp": h["timestamp"], "type": "CRITICAL_OVERLOAD",
                           "message": "Critical overload: predicted demand %.2f kW is at or above %g kW"
                                      % (h["predicted_demand_kw"], OVERLOAD_KW)})
        if h["battery_limited"]:
            alerts.append({"level": "warning", "timestamp": h["timestamp"], "type": "BATTERY_RESERVE",
                           "message": "Battery reserve reached: storage mode needs %.2f kW but the battery supplies "
                                      "%.2f kW; the grid covers the rest"
                                      % (h["predicted_demand_kw"], -h["battery_kw"])})
    return alerts


def summarize(hours):
    if not hours:
        return {}
    peak = max(hours, key=lambda h: h["predicted_demand_kw"])
    dem = [h["predicted_demand_kw"] for h in hours]
    soc = [h["soc_pct"] for h in hours]
    return {
        "hours": len(hours),
        "peak_demand_kw": peak["predicted_demand_kw"],
        "peak_time": peak["timestamp"],
        "mean_demand_kw": round(sum(dem) / len(dem), 2),
        "total_energy_kwh": round(sum(dem), 1),
        "storage_hours": sum(1 for h in hours if h["grid_mode"] == MODE_STORAGE),
        "grid_hours": sum(1 for h in hours if h["grid_mode"] == MODE_GRID),
        "overload_hours": sum(1 for h in hours if h["overload"]),
        "battery_limited_hours": sum(1 for h in hours if h["battery_limited"]),
        "min_soc_pct": min(soc),
        "final_soc_pct": soc[-1],
    }


# ----------------------------------------------------------------------------- orchestration + log
class RunLog:
    """Last N forecast runs. Held in memory and best-effort mirrored to a JSONL file in the
    temp dir (AppSail does not allow writes to the app directory)."""

    def __init__(self, n=40):
        self._items = deque(maxlen=n)
        self._lock = threading.Lock()
        self._path = os.path.join(tempfile.gettempdir(), "hacksphere_runs.jsonl")
        try:
            with open(self._path) as fh:
                for line in fh.readlines()[-n:]:
                    self._items.append(json.loads(line))
        except (OSError, ValueError):
            pass

    def add(self, item):
        with self._lock:
            self._items.append(item)
            try:
                with open(self._path, "a") as fh:
                    fh.write(json.dumps(item) + "\n")
            except OSError:
                pass

    def all(self):
        with self._lock:
            return list(self._items)[::-1]


LOG = RunLog()


def forecast(raw_rows, battery=None, origin="api"):
    rows, report = clean_rows(raw_rows)
    t0 = time.time()
    result = predictor.forecast_rows(rows)
    latency_ms = int((time.time() - t0) * 1000)
    preds = result["predictions"]
    if len(preds) != len(rows):
        raise RuntimeError("Model returned %d predictions for %d rows." % (len(preds), len(rows)))
    bat = sanitize_battery(battery)
    hours = run_controller(rows, preds, bat)
    summary = summarize(hours)
    resp = {
        "ok": True,
        "cleaning": report,
        "battery": bat,
        "hours": hours,
        "signals": build_signals(hours),
        "alerts": build_alerts(hours),
        "summary": summary,
        "source": {"name": result["source"], "detail": result["detail"], "latency_ms": latency_ms},
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") + " UTC",
    }
    LOG.add({
        "at": resp["generated_at"], "origin": origin, "rows": len(hours), "source": result["source"],
        "peak_kw": summary["peak_demand_kw"], "storage_hours": summary["storage_hours"],
        "overload_hours": summary["overload_hours"], "latency_ms": latency_ms,
        "start": hours[0]["timestamp"], "end": hours[-1]["timestamp"],
    })
    return resp


# ----------------------------------------------------------------------------- demo windows
def demo_windows(csv_path):
    """Three full-day (00:00-23:00) windows from the sample test data: hottest, typical, coolest."""
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    days = {}
    for r in rows:
        ts = parse_timestamp(r["timestamp"])
        if ts:
            days.setdefault(ts.date(), []).append(r)
    full = [(d, rs) for d, rs in days.items() if len(rs) == 24]
    if not full:
        return []
    full.sort(key=lambda x: sum(float(r["ambient_temp_c"]) for r in x[1]) / 24.0)
    picks = [("Hottest day", full[-1]), ("Typical day", full[len(full) // 2]), ("Coolest day", full[0])]
    out = []
    for label, (d, rs) in picks:
        avg = sum(float(r["ambient_temp_c"]) for r in rs) / 24.0
        out.append({"label": "%s (%s, avg %.1f C)" % (label, d.isoformat(), avg), "rows": rs})
    return out
