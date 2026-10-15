"""Sends steady traffic to the demo app so dashboards and SLO rules have data."""
import os
import time
import urllib.request

URL = os.getenv("TARGET_URL", "http://127.0.0.1:8000/checkout")
RPS = float(os.getenv("RPS", "5"))

while True:
    try:
        urllib.request.urlopen(urllib.request.Request(URL, method="POST"), timeout=5).read()
    except Exception:  # errors are expected; the app records them as metrics
        pass
    time.sleep(1 / RPS)
