#!/usr/bin/env bash
# Runs every automated check. Exit code != 0 if anything fails.
set -euo pipefail
cd "$(dirname "$0")/.."
unset QUICKML_ENDPOINT_URL
echo "== engine / controller rules";  python3 tests/test_engine.py 2>&1 | grep -v '^Warning'
echo "== QuickML client (mock server)"; python3 tests/test_quickml_client.py 2>&1 | grep -v '^Warning'
echo "== official self-test";          python3 test_eval.py 2>&1 | tail -2
echo "== appsail/ copy is in sync"
./scripts/build_appsail.sh >/dev/null
for f in app.py engine.py predict.py model_fallback.json; do cmp -s "$f" "appsail/$f" || { echo "OUT OF SYNC: $f"; exit 1; }; done
echo "all checks passed"
