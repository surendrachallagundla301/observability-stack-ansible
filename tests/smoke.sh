#!/usr/bin/env bash
# End-to-end check after `ansible-playbook site.yml`: every component is up,
# all three telemetry paths carry data, and the SLO recording rules evaluate.
set -euo pipefail
PROM=${PROM:-http://127.0.0.1:9090}
SKIP_GRAFANA=${SKIP_GRAFANA:-false}
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "ok   $*"; }

wait_for() {  # wait_for <url> <name>
  for _ in $(seq 1 30); do curl -fsS -o /dev/null "$1" && { ok "$2 is up"; return; }; sleep 2; done
  fail "$2 did not answer at $1"
}
prom_query() { curl -fsS --get "$PROM/api/v1/query" --data-urlencode "query=$1" | python3 -c 'import json,sys; r=json.load(sys.stdin)["data"]["result"]; print(r[0]["value"][1] if r else "")'; }

wait_for "$PROM/-/ready" Prometheus
wait_for http://127.0.0.1:9093/-/ready Alertmanager
wait_for http://127.0.0.1:9100/metrics node_exporter
wait_for http://127.0.0.1:9102/metrics statsd_exporter
wait_for http://127.0.0.1:9598/metrics "Vector"
wait_for http://127.0.0.1:8000/healthz "demo app"
[ "$SKIP_GRAFANA" = true ] || wait_for http://127.0.0.1:3000/api/health Grafana

echo "Sending traffic to the demo app..."
for _ in $(seq 1 50); do curl -fsS -o /dev/null -X POST http://127.0.0.1:8000/checkout || true; done

echo "Waiting for Prometheus to scrape and evaluate rules..."
for _ in $(seq 1 30); do
  [ -n "$(prom_query 'slo:sli_error:ratio_rate5m{service="demo-app"}')" ] && break; sleep 5
done

[ -n "$(prom_query 'sum(http_requests_total{job="demo-app"})')" ] || fail "no app request metrics in Prometheus"
ok "metrics: demo app request counters are in Prometheus"
[ -n "$(prom_query 'sum(statsd_app_events_total)')" ] || fail "no StatsD events in Prometheus"
ok "metrics: StatsD business events reach Prometheus via statsd_exporter"
[ -n "$(prom_query 'sum(log_events_total)')" ] || fail "no log-derived metrics from Vector"
ok "logs: Vector parses app logs and exposes log_events_total"
grep -q '"trace_id": "[0-9a-f]\{32\}"' /var/log/observability/demo-app/app.log || fail "logs carry no trace_id"
ok "traces: log lines carry OpenTelemetry trace IDs"
[ -n "$(prom_query 'slo:sli_error:ratio_rate5m{service="demo-app"}')" ] || fail "SLO recording rule has no data"
ok "SLO: recording rules evaluate (error ratio 5m = $(prom_query 'slo:sli_error:ratio_rate5m{service="demo-app"}'))"

down=$(prom_query 'count(up == 0) or vector(0)')
[ "$down" = "0" ] || fail "$down scrape targets are down"
ok "all scrape targets are up"
echo "Smoke test passed."
