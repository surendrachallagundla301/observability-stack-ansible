"""Demo checkout service instrumented with all three telemetry signals.

* Metrics: Prometheus counters/histograms on /metrics, plus StatsD business events.
* Logs:    one JSON line per request, carrying the trace_id so logs and traces link up.
* Traces:  OpenTelemetry spans, exported to Honeycomb when HONEYCOMB_API_KEY is set.

ERROR_RATE (0.0-1.0) controls how often /checkout fails, so you can trigger
the SLO burn-rate alerts on purpose.
"""
import json
import logging
import os
import random
import time

import statsd
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

SERVICE = os.getenv("SERVICE_NAME", "demo-app")
ERROR_RATE = float(os.getenv("ERROR_RATE", "0.0002"))
LOG_FILE = os.getenv("LOG_FILE", "app.log")

# ---------- traces ----------
provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
if os.getenv("HONEYCOMB_API_KEY"):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint="https://api.honeycomb.io/v1/traces",
                headers={"x-honeycomb-team": os.environ["HONEYCOMB_API_KEY"]},
            )
        )
    )
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(SERVICE)

# ---------- metrics ----------
REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
events = statsd.StatsClient(
    os.getenv("STATSD_HOST", "127.0.0.1"), int(os.getenv("STATSD_PORT", "9125")), prefix="demo_app"
)

# ---------- logs ----------
handler = logging.FileHandler(LOG_FILE)
handler.setFormatter(logging.Formatter("%(message)s"))
log = logging.getLogger(SERVICE)
log.addHandler(handler)
log.setLevel(logging.INFO)


def log_json(level: str, msg: str, **fields) -> None:
    ctx = trace.get_current_span().get_span_context()
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "level": level,
        "service": SERVICE,
        "msg": msg,
        "trace_id": format(ctx.trace_id, "032x") if ctx.is_valid else None,
        **fields,
    }
    log.log(logging.ERROR if level == "error" else logging.INFO, json.dumps(record))


app = FastAPI(title=SERVICE)
UNTRACKED = {"/metrics", "/healthz"}


@app.middleware("http")
async def telemetry(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = route.path if route else "unmatched"  # route template keeps label cardinality low
    if path not in UNTRACKED:
        elapsed = time.perf_counter() - start
        REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        LATENCY.labels(path).observe(elapsed)
        level = "error" if response.status_code >= 500 else "info"
        log_json(level, "request", method=request.method, route=path,
                 status=response.status_code, duration_ms=round(elapsed * 1000, 2))
    return response


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/checkout")
def checkout():
    with tracer.start_as_current_span("charge_card") as span:
        start = time.perf_counter()
        time.sleep(random.uniform(0.005, 0.05))
        failed = random.random() < ERROR_RATE
        span.set_attribute("checkout.failed", failed)
        events.timing("checkout.duration_ms", (time.perf_counter() - start) * 1000)
    if failed:
        events.incr("checkout.failed")
        return JSONResponse({"error": "payment provider unavailable"}, status_code=503)
    events.incr("checkout.orders")
    return {"status": "confirmed"}


@app.post("/alertmanager-webhook")
async def alertmanager_webhook(request: Request):
    """Stand-in pager: logs every alert Alertmanager routes here."""
    body = await request.json()
    for alert in body.get("alerts", []):
        log_json("warning", "alert received", receiver=request.query_params.get("receiver"),
                 alertname=alert["labels"].get("alertname"), alert_status=alert.get("status"))
    return {"received": len(body.get("alerts", []))}


FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,healthz")
