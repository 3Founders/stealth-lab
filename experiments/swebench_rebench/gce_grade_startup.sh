#!/bin/bash
# Grades SWE-bench-style predictions with the OFFICIAL harness + native Docker on a Compute Engine VM, then
# uploads reports to GCS and powers the VM off. Settings come from instance metadata:
#   kel-bucket      gs://... (inputs under <prefix>/inputs, results under <prefix>/results/<run-id>)
#   kel-prefix      e.g. rebench
#   kel-ids         file under inputs/ with one instance id per line
#   kel-preds       "gold" or a predictions file under inputs/
#   kel-run-id      harness run id (reports are cached per run id, so a rerun resumes)
#   kel-workers     harness --max_workers
#   kel-max-hours   hard power-off after this many hours (cost guard)
set -uxo pipefail
md() { curl -sf -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }
BUCKET=$(md kel-bucket); PREFIX=$(md kel-prefix); IDS=$(md kel-ids); PREDS=$(md kel-preds)
RUN_ID=$(md kel-run-id); WORKERS=$(md kel-workers); MAXH=$(md kel-max-hours)
shutdown -h +$(( ${MAXH:-3} * 60 ))            # cost guard: power off no matter what

OUT="$BUCKET/$PREFIX/results/$RUN_ID"
: > /var/log/kel-grade.log                        # one log per boot
exec > >(tee -a /var/log/kel-grade.log) 2>&1

if ! command -v docker >/dev/null; then apt-get update && apt-get install -y docker.io python3-venv git; fi
if ! command -v gcloud >/dev/null; then snap install google-cloud-cli --classic || true; fi
python3 -m venv /opt/swb && /opt/swb/bin/pip install -q "swebench==5.0.2" datasets

rm -rf /work && mkdir -p /work && cd /work        # fresh each boot: resume state comes only from GCS
gcloud storage cp "$BUCKET/$PREFIX/inputs/*" /work/
# Resume: bring back any reports a previous run of this run id already uploaded
gcloud storage rsync -r "$OUT/logs" /work/logs 2>/dev/null || true

# Upload progress every 2 minutes
( while true; do sleep 120; gcloud storage rsync -r /work/logs "$OUT/logs" >/dev/null 2>&1;
    gcloud storage cp /var/log/kel-grade.log "$OUT/grade.log" >/dev/null 2>&1; done ) &

PRED_ARG="$PREDS"; [ "$PREDS" != "gold" ] && PRED_ARG="/work/$PREDS"
START=$(date +%s)
/opt/swb/bin/python -m swebench.harness.run_evaluation \
    --dataset_name /work/grading_dataset.json --split test --predictions_path "$PRED_ARG" \
    --run_id "$RUN_ID" --max_workers "$WORKERS" --timeout 1800 --instance_ids $(tr -d '\r' < "/work/$IDS")   # ids files written on Windows carry CR
echo "harness exit $? after $(( $(date +%s) - START ))s"
df -h / ; docker system df

gcloud storage rsync -r /work/logs "$OUT/logs"
gcloud storage cp "/work/"*".$RUN_ID.json" "$OUT/" 2>/dev/null || true   # run summary only, not the inputs
gcloud storage cp /var/log/kel-grade.log "$OUT/grade.log"
echo DONE | gcloud storage cp - "$OUT/DONE"
shutdown -h now
