import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("QUICKML_ENDPOINT_URL", None)
import engine
from engine import classify, MODE_STORAGE as S, MODE_GRID as G

# --- Section 6 rules, every boundary ---------------------------------------------------
cases = [
    (160.00, 10, G, False), (160.01, 10, S, False), (159.99, 10, G, False),
    (100.0, 17, G, False), (100.0, 18, S, False), (100.0, 22, S, False), (100.0, 23, G, False),
    (199.99, 3, S, False), (200.00, 3, S, True), (250.0, 19, S, True), (90.0, 0, G, False),
    (200.0, 12, S, True), (160.0, 18, S, False), (160.0, 22, S, False), (160.0, 23, G, False),
]
for demand, hour, mode, over in cases:
    m, o, reasons = classify(demand, hour)
    assert (m, o) == (mode, over), (demand, hour, m, o)
    assert reasons
print("controller: %d boundary cases OK" % len(cases))

# --- overload is independent of mode ----------------------------------------------------
rows = [{"timestamp": "2026-10-28 %02d:00:00" % h, "ambient_temp_c": 30, "humidity_pct": 50, "solar_irradiance_wm2": 0} for h in (3, 10, 17, 18, 22, 23)]
preds = [210, 165, 150, 150, 150, 120]  # 03h overload in Storage (>160), 10h storage, 17h grid, 18h/22h window, 23h grid
hours = engine.run_controller(rows, preds, engine.sanitize_battery({}))
got = [(h["grid_mode"][:5], h["overload"]) for h in hours]
assert got == [("Renew", True), ("Renew", False), ("Main ", False), ("Renew", False), ("Renew", False), ("Main ", False)], got
sig = engine.build_signals(hours)
assert [s["signal"] for s in sig] == ["SWITCH_TO_RENEWABLE_STORAGE", "SWITCH_TO_MAIN_GRID", "SWITCH_TO_RENEWABLE_STORAGE", "SWITCH_TO_MAIN_GRID"], sig
assert sig[0]["initial"] and not sig[1]["initial"]
print("controller: mode sequence, independent overload, transition signals OK")

# --- battery invariants -----------------------------------------------------------------
bat = engine.sanitize_battery({"capacity_kwh": 1000, "initial_soc_pct": 50, "max_discharge_kw": 300, "max_charge_kw": 100, "reserve_pct": 10})
rows = [{"timestamp": "2026-10-28 %02d:00:00" % h, "ambient_temp_c": 30, "humidity_pct": 50, "solar_irradiance_wm2": 0} for h in range(24)]
hs = engine.run_controller(rows, [170] * 24, bat)  # storage all day
assert all(0 <= h["soc_pct"] <= 100 for h in hs)
assert min(h["soc_pct"] for h in hs) >= 10 - 1e-6, "must never go below reserve"
assert any(h["battery_limited"] for h in hs) and hs[0]["battery_limited"] is False
hs = engine.run_controller(rows, [100] * 24, bat)  # grid all day (outside window 18-22 -> storage there)
assert max(h["soc_pct"] for h in hs) <= 100 + 1e-6
for h in hs: assert abs(h["grid_import_kw"] - (h["predicted_demand_kw"] - (-h["battery_kw"] if h["battery_kw"] < 0 else 0) + (h["battery_kw"] if h["battery_kw"] > 0 else 0))) < 0.011
print("battery: bounds, reserve, energy balance OK")

# --- ingestion ---------------------------------------------------------------------------
def raises(fn, exc):
    try: fn()
    except exc: return True
    return False

assert raises(lambda: engine.clean_rows([]), engine.ValidationError)
assert raises(lambda: engine.clean_rows([{"a": 1}]), engine.ValidationError)
assert raises(lambda: engine.clean_rows([{"timestamp": "garbage", "temp": 1}]), engine.ValidationError)
rows, rep = engine.clean_rows([{"timestamp": "2026-10-28 02:00:00", "ambient_temp_c": "30", "humidity_pct": "50", "solar_irradiance_wm2": "0"},
                               {"timestamp": "2026-10-28 00:00:00", "ambient_temp_c": "20", "humidity_pct": "", "solar_irradiance_wm2": "0"},
                               {"timestamp": "2026-10-28 01:00:00", "ambient_temp_c": "", "humidity_pct": "60", "solar_irradiance_wm2": "0"}])
assert [r["timestamp"][11:13] for r in rows] == ["00", "01", "02"]
assert rows[1]["ambient_temp_c"] == 25.0 and rows[0]["humidity_pct"] == 60.0, rows   # interpolated / edge-filled
assert len(engine.clean_rows([{"timestamp": "2026-01-01 %02d:00:00" % (i % 24) if i < 24 else "2026-01-%02d %02d:00:00" % (2 + i // 24, i % 24), "ambient_temp_c": 20, "humidity_pct": 50, "solar_irradiance_wm2": 0} for i in range(300)])[0]) == engine.MAX_ROWS
print("ingestion: validation, ordering, imputation, row cap OK")

# --- end to end -------------------------------------------------------------------------
res = engine.forecast(rows, None, origin="test")
assert len(res["hours"]) == 3 and res["source"]["name"] in ("local_fallback", "quickml") and res["summary"]["hours"] == 3
print("ALL ENGINE TESTS PASSED")
