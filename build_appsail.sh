#!/usr/bin/env bash
# Copies the files AppSail needs into ./appsail (the folder you deploy).  Run after editing any source file.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf appsail/static appsail/data
mkdir -p appsail/data
cp app.py engine.py predict.py model_fallback.json appsail/
cp -r static appsail/static
cp sample_test_input.csv appsail/data/sample_test_input.csv
echo "appsail/ is up to date:"; find appsail -type f | sort | sed 's/^/  /'
