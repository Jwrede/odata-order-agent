#!/usr/bin/env bash
# One mail through agent (replayed LLM answers, live CAP service), gate and human approval.
set -euo pipefail
cd "$(dirname "$0")/.."
export PORT=${PORT:-4004} ORDER_TODAY=2026-11-27
export OOA_ODATA_URL=http://127.0.0.1:$PORT/odata/v4/orders
(cd cap && exec node node_modules/@sap/cds/bin/serve.js > "${TMPDIR:-/tmp}/ooa-cap.log" 2>&1) &
CAP=$!
trap 'kill $CAP' EXIT
for _ in $(seq 100); do curl -fs -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 0.3; done
mail="${TMPDIR:-/tmp}/ooa-pb-02.json"
grep '"id": "pb-02"' eval/mails.jsonl > "$mail"
echo "== agent + gate"
order-agent process "$mail" --model sonnet --mode replay
echo "== queue for a person"
order-agent queue
echo "== approve"
order-agent approve "$(order-agent queue | awk 'NR==1{print $1}')" --note "checked against the framework contract"
