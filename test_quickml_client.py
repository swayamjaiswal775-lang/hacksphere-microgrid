import os, sys, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import mock_quickml as mq

srv = mq.start(); port = srv.server_address[1]; base = "http://127.0.0.1:%d" % port
ENV = {"QUICKML_ENDPOINT_URL": base + "/predict", "QUICKML_ENDPOINT_KEY": "KEY123", "CATALYST_ORG_ID": "ORG9",
       "ZOHO_ACCOUNTS_URL": base, "ZOHO_CLIENT_ID": "cid", "ZOHO_CLIENT_SECRET": "sec", "ZOHO_REFRESH_TOKEN": "ref",
       "QUICKML_TIMEOUT": "5"}

def fresh(**extra):
    for k in list(os.environ):
        if k.startswith(("QUICKML_", "ZOHO_", "CATALYST_")): del os.environ[k]
    os.environ.update(ENV); os.environ.update(extra)
    import predict; importlib.reload(predict); return predict

rows = [{"timestamp": "2026-10-28 %02d:00:00" % h, "ambient_temp_c": 30 + h / 10, "humidity_pct": 50, "solar_irradiance_wm2": 100} for h in range(24)]
expected = [100 + h + 0.5 * (30 + h / 10) for h in range(24)]

# 1. batch contract, token refresh
mq.State.mode = "batch"; p = fresh()
r = p.forecast_rows(rows)
assert r["source"] == "quickml" and len(r["predictions"]) == 24 and max(abs(a - b) for a, b in zip(r["predictions"], expected)) < 1e-9, r
assert p._CLIENT.mode == "batch"; print("1 batch mode + oauth refresh + headers OK")

# 2. flat one-row-per-call API: auto mode must discover it
mq.State.mode = "single"; p = fresh()
r = p.forecast_rows(rows)
assert r["source"] == "quickml" and p._CLIENT.mode == "single" and max(abs(a - b) for a, b in zip(r["predictions"], expected)) < 1e-9, r
print("2 auto-detects single-row API, order preserved across 8 threads OK")

# 3. expired token mid-flight (401) -> refresh once and retry
mq.State.mode = "batch"; p = fresh(); p.forecast_rows(rows[:2]); before = mq.State.tokens_issued
mq.State.reject_first_predict_with_401 = True
r = p.forecast_rows(rows); assert r["source"] == "quickml" and mq.State.tokens_issued == before + 1
print("3 401 -> token refreshed -> retried OK")

# 4. endpoint down: fallback allowed -> local model; fallback disabled -> raises
mq.State.fail_with_500 = True
p = fresh(QUICKML_MODE="batch"); r = p.forecast_rows(rows); assert r["source"] == "local_fallback" and len(r["predictions"]) == 24
p = fresh(QUICKML_MODE="batch", QUICKML_FALLBACK="0")
try: p.forecast_rows(rows); raise SystemExit("should have raised")
except p.QuickMLError: pass
mq.State.fail_with_500 = False; print("4 500s -> fallback (or loud failure with QUICKML_FALLBACK=0) OK")

# 5. wrong endpoint key -> not silently accepted
p = fresh(QUICKML_ENDPOINT_KEY="WRONG", QUICKML_FALLBACK="0")
try: p.forecast_rows(rows); raise SystemExit("should have raised")
except p.QuickMLError as e: assert "403" in str(e) or "400" in str(e), e
print("5 bad credentials surface as an error OK")

# 6. response-shape tolerance
p = fresh()
for shape, want in [({"predictions": [1, 2]}, [1, 2]), ({"result": [1.5, 2.5]}, [1.5, 2.5]), ({"data": {"prediction": [3]}}, [3]),
                    ([{"target_demand_kw": 4}, {"target_demand_kw": "5"}], [4, 5]), ({"status": "success", "result": 7}, [7]), ({"x": "y"}, None)]:
    assert p._extract_numbers(shape) == want, (shape, p._extract_numbers(shape))
print("6 response parsing OK")
print("ALL CLIENT TESTS PASSED")
