#!/usr/bin/env bash
# Oracle Cloud VM / container launch path for the ingestion worker.
# Identical worker code to Cloud Run / GitHub Actions / local -- only the launcher differs.
#
#   1. put secrets in /etc/stealth/ingest.env (chmod 600, NOT in git):
#        CONTROL_DATABASE_URL=...   GEMINI_API_KEY=...   VOYAGE_API_KEY=...   JEV_BASE_URL=...   JEV_API_KEY=...
#        STEALTHLAB_ENV=PRODUCTION  INGEST_WORKER_CONCURRENCY=4
#   2. ./run-worker.sh            # long-running (systemd/docker restart policy handles crashes)
#      ./run-worker.sh --once     # drain then exit (cron)
#
# Scale out by starting more of these on more VMs (or `docker run` N times): workers are
# independent; the queue leases jobs to whoever asks first.
set -euo pipefail

IMAGE="${STEALTH_WORKER_IMAGE:-stealth-ingest-worker:latest}"
ENV_FILE="${STEALTH_ENV_FILE:-/etc/stealth/ingest.env}"
MODE="${1:---loop}"

exec docker run --rm --name "stealth-ingest-$(hostname)-$$" \
  --env-file "$ENV_FILE" \
  -e INGEST_WORKER_ID="oci-$(hostname)-$$" \
  --restart=no \
  "$IMAGE" "$MODE"

# --- systemd unit (save as /etc/systemd/system/stealth-ingest.service) -------------------------
# [Unit]
# Description=Stealth ingestion worker
# After=network-online.target docker.service
# [Service]
# Restart=always
# RestartSec=10
# ExecStart=/opt/stealth/deploy/ingestion/oracle/run-worker.sh --loop
# [Install]
# WantedBy=multi-user.target
