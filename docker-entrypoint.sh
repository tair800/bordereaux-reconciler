#!/bin/sh
# Seed, then serve.
#
# `set -e` deliberately absent. A seeding failure must not stop the container: the evidence screen
# is artifact-backed and works without a database at all, `/healthz` reports the real state, and a
# console that serves five screens and explains the sixth is worth more to a visitor than one that
# crash-loops. The failure is loud in the logs and visible at /healthz, which is where an operator
# looks.
#
# `exec` on the last line so uvicorn becomes PID 1 and receives SIGTERM directly. Without it the
# shell holds PID 1, swallows the signal, and every deploy waits out the platform's kill timeout.

echo "[entrypoint] seeding"
python /app/scripts/seed_demo.py || echo "[entrypoint] seeding failed; serving anyway, /healthz will say so"

echo "[entrypoint] starting uvicorn on ${PORT:-8000}"
exec uvicorn bordereaux_reconciler.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
