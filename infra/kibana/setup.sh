#!/bin/sh
# Kibana에 데이터 뷰와 운영 대시보드를 넣는다(kibana-setup 서비스가 실행).
# 다시 돌려도 안전하다: 객체 id가 고정이라 덮어쓴다. 실패하면(success가 없으면) 종료 코드로 알린다.
set -eu
KIBANA="${KIBANA_URL:-http://kibana:5601}"
curl -sf -X POST "$KIBANA/api/saved_objects/_import?overwrite=true" \
  -H "kbn-xsrf: true" --form file=@/setup/dashboard.ndjson -o /tmp/result.json
cat /tmp/result.json; echo
grep -q '"success":true' /tmp/result.json
echo "dashboard: $KIBANA/app/dashboards#/view/volley-ops"
