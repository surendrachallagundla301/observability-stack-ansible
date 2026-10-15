# Runbook

Every alert links here. Each section says what the alert means, how to confirm it, and what to do first.

## SLOErrorBudgetBurnFast

**Meaning:** `demo-app` is failing requests at more than 14.4x the rate its 99.95% SLO allows. At this pace about 2% of the monthly error budget is gone every hour, and the whole budget is gone in about two days.

**Confirm:**
1. Open the **SLO Overview** dashboard in Grafana and check *Error budget burn rate by window*.
2. In Prometheus, break errors down by route: `sum by (route, status) (rate(http_requests_total{job="demo-app",status=~"5.."}[5m]))`
3. Pull matching log lines: `grep '"level": "error"' /var/log/observability/demo-app/app.log | tail`. Each line has a `trace_id` you can look up in Honeycomb to see the failing span.

**Act:** roll back the most recent deploy if the errors started with it. Otherwise follow the failing span to the dependency that is breaking.

## SLOErrorBudgetBurnMedium

**Meaning:** errors are running at more than 6x the allowed rate over 6 hours. Slower than a fast burn, but it still exhausts the budget in about five days.

**Act:** same steps as the fast burn. There is usually time to find the root cause before rolling back.

## SLOErrorBudgetBurnSlow

**Meaning:** a ticket, not a page. Errors have been running at 3x the allowed rate for a day.

**Act:** open a ticket, find the route or dependency responsible, and fix it during working hours.

## SLOErrorBudgetBurnSteady

**Meaning:** a ticket. The service has been slightly over budget (1x) for three days and will miss its SLO if nothing changes.

**Act:** review recent changes and the error breakdown by route; plan a fix within the week.

## TargetDown

**Meaning:** Prometheus has failed to scrape a target for 2 minutes. That component may be down, or the network path to it is broken.

**Confirm:** `systemctl status <service>` and `journalctl -u <service> -n 100` on the host shown in the `instance` label.

**Act:** restart the service if it crashed. If it keeps crashing, check the last config change (every config is validated before it is written, so a crash usually means resources or the host).

## NodeFilesystemAlmostFull

**Meaning:** a filesystem has less than 10% space left.

**Act:** `du -xh --max-depth=1 <mountpoint> | sort -h | tail`. Common culprits are Prometheus data (`/var/lib/observability/prometheus`, lower `prometheus_retention`) and Vector's log archive (`/var/log/observability/archive`).

## ApplicationErrorLogSpike

**Meaning:** Vector is counting more than one error-level log line per second for a service. This can fire before the SLO alerts when errors don't reach users (for example, retried background work).

**Act:** read the recent error lines and follow their `trace_id`s.
