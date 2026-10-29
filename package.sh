#!/usr/bin/env bash
# Creates TeamName_Submission.zip (no secrets, no caches). Usage: scripts/package.sh MyTeam
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:-TeamName}_Submission"
./scripts/build_appsail.sh >/dev/null
rm -f "../$NAME.zip"
zip -rq "../$NAME.zip" . -x '*/__pycache__/*' '__pycache__/*' '.env' '*.pyc' 'predictions.csv'
echo "wrote ../$NAME.zip"
