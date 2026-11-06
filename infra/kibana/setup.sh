#!/bin/sh
# Import the data views and the operations dashboard into Kibana (run by the kibana-setup service).
# Re-running is safe: objects keep fixed ids and are overwritten.
set -eu
KIBANA="${KIBANA_URL:-http://kibana:5601}"
curl -sf -X POST "$KIBANA/api/saved_objects/_import?overwrite=true" \
  -H "kbn-xsrf: true" --form file=@/setup/dashboard.ndjson -o /tmp/result.json
cat /tmp/result.json; echo
grep -q '"success":true' /tmp/result.json
echo "dashboard: $KIBANA/app/dashboards#/view/volley-ops"
