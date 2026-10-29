"""Tiny fake Zoho OAuth + QuickML endpoint used by tests/test_quickml_client.py (dev only)."""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class State:
    mode = "batch"          # "batch" -> accepts {"data":[...]}, "single" -> accepts one flat dict per call
    tokens_issued = 0
    valid_token = None
    reject_first_predict_with_401 = False
    fail_with_500 = False
    calls = []

def fake_model(f):
    return 100.0 + f["hour"] + 0.5 * f["ambient_temp_c"]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, code, obj):
        d = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n)
        if self.path.startswith("/oauth/v2/token"):
            State.tokens_issued += 1; State.valid_token = "tok-%d" % State.tokens_issued
            return self._json(200, {"access_token": State.valid_token, "expires_in": 3600})
        State.calls.append(self.path)
        h = self.headers
        if State.fail_with_500: return self._json(500, {"message": "boom"})
        if State.reject_first_predict_with_401:
            State.reject_first_predict_with_401 = False; return self._json(401, {"message": "INVALID_OAUTHTOKEN"})
        if h.get("Authorization") != "Zoho-oauthtoken " + str(State.valid_token): return self._json(401, {"message": "bad token"})
        if h.get("X-QUICKML-ENDPOINT-KEY") != "KEY123" or h.get("CATALYST-ORG") != "ORG9" or h.get("Environment") != "Development":
            return self._json(403, {"message": "bad headers %s" % dict(h)})
        body = json.loads(raw)
        if State.mode == "batch":
            if "data" not in body: return self._json(400, {"message": "expected data"})
            return self._json(200, {"predictions": [fake_model(r) for r in body["data"]]})
        if "data" in body or "hour" not in body: return self._json(400, {"message": "expected flat row"})
        return self._json(200, {"status": "success", "result": [fake_model(body)]})

def start(port=0):
    srv = HTTPServer(("127.0.0.1", port), H); threading.Thread(target=srv.serve_forever, daemon=True).start(); return srv
