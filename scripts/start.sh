#!/bin/sh
# Container entrypoint (Dockerfile CMD; Render uses it as is).
#   1. migrations  2. demo data, only in demo mode (no-op once loaded)  3. uvicorn on $PORT
# Settings validation runs in step 1: in production a missing secret stops the start here.
set -e

alembic upgrade head

case "$(printf '%s' "${ERP_DEMO_MODE:-}" | tr '[:upper:]' '[:lower:]')" in
    true | 1 | yes) python -m scripts.seed_demo ;;
esac

# exec: uvicorn becomes PID 1 and receives the platform's stop signals directly.
# --proxy-headers: TLS ends at the provider's proxy, so generated URLs must be https.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-10000}" \
    --proxy-headers \
    --forwarded-allow-ips '*'
