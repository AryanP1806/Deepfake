"""Serve the DEMO fixture "web" on http://127.0.0.1:8765 (needed for re-capture and monitoring of the demo case)."""
import time

from make_demo_case import BASE, SITE, serve

if __name__ == "__main__":
    if not SITE.exists():
        raise SystemExit("Fixture site missing — run make_demo_case.py first.")
    serve()
    print(f"Serving DEMO fixtures from {SITE} at {BASE}")
    while True:
        time.sleep(3600)
