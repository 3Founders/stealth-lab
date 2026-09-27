#!/bin/bash
# Grades SWE-bench-style predictions with the OFFICIAL harness (swebench 5.0.2, unmodified) + native Docker on a
# Compute Engine VM. Two modes, chosen by metadata kel-mode:
#   oneshot (default)  grade one ids file once, upload, power off        (smoke tests)
#   daemon             poll a GCS queue (experiments/swebench/gce_queue.py), grade batches as they arrive,
#                      upload reports after each harness call, power off after kel-idle-min idle minutes
# Metadata: kel-bucket, kel-prefix, kel-run-id, kel-ids, kel-preds (oneshot), kel-worker, kel-dataset,
#           kel-idle-min (daemon), kel-workers (harness --max_workers), kel-max-hours (hard power-off guard).
set -uxo pipefail
md() { curl -sf -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }
MODE=$(md kel-mode || echo oneshot); BUCKET=$(md kel-bucket); PREFIX=$(md kel-prefix)
WORKERS=$(md kel-workers); MAXH=$(md kel-max-hours)
shutdown -h +$(( ${MAXH:-3} * 60 ))            # cost guard: power off no matter what
BASE="$BUCKET/$PREFIX"
: > /var/log/kel-grade.log                        # one log per boot
exec > >(tee -a /var/log/kel-grade.log) 2>&1

if ! command -v docker >/dev/null; then apt-get update && apt-get install -y docker.io python3-venv git; fi
if ! command -v gcloud >/dev/null; then snap install google-cloud-cli --classic || true; fi
[ -x /opt/swb/bin/python ] || { python3 -m venv /opt/swb && /opt/swb/bin/pip install -q "swebench==5.0.2" datasets; }
rm -rf /work && mkdir -p /work && cd /work        # fresh each boot: resume state comes only from GCS
gcloud storage cp "$BASE/inputs/*" /work/ 2>/dev/null || true

harness() {  # $1 predictions file, $2 run id, $3 dataset; instance ids come from the predictions file
    local dataset="$3"; [ -f "/work/$dataset" ] && dataset="/work/$dataset"
    local ids; ids=$(/opt/swb/bin/python -c "import json,sys;print(' '.join(json.loads(l)['instance_id'] for l in open(sys.argv[1]) if l.strip()))" "$1")
    local t0; t0=$(date +%s)
    /opt/swb/bin/python -m swebench.harness.run_evaluation --dataset_name "$dataset" --split test \
        --predictions_path "$1" --run_id "$2" --max_workers "$WORKERS" --timeout 1800 --instance_ids $ids
    local rc=$?
    echo "harness run_id=$2 n=$(echo $ids | wc -w) exit=$rc seconds=$(( $(date +%s) - t0 ))"
    gcloud storage rsync -r "/work/logs/run_evaluation/$2" "$BASE/results/$2/logs/run_evaluation/$2"
}

if [ "$MODE" = "daemon" ]; then
    WORKER=$(md kel-worker); DATASET=$(md kel-dataset); IDLE=$(md kel-idle-min)
    # background pre-pull of this worker's shard (images are reused by every arm graded here)
    [ -f "/work/prepull_$WORKER.txt" ] && ( grep -v '^$' "/work/prepull_$WORKER.txt" | xargs -r -P 3 -n 1 docker pull -q >/dev/null 2>&1 & )
    ( while true; do sleep 120; gcloud storage cp /var/log/kel-grade.log "$BASE/results/_workers/$WORKER.log" >/dev/null 2>&1; done ) &
    idle=0
    while true; do
        batches=$(gcloud storage ls "$BASE/queue/$WORKER/" 2>/dev/null | grep '\.jsonl$' | sort)
        if [ -z "$batches" ]; then
            idle=$(( idle + 20 )); [ "$idle" -ge $(( ${IDLE:-20} * 60 )) ] && break; sleep 20; continue
        fi
        idle=0
        run=$(basename "$(echo "$batches" | head -1)" | sed 's/__[0-9]*\.jsonl$//')
        mine=$(echo "$batches" | grep "/${run}__")
        : > "/work/batch.jsonl"
        for b in $mine; do gcloud storage cat "$b" >> /work/batch.jsonl; done
        harness /work/batch.jsonl "$run" "$DATASET"
        for b in $mine; do gcloud storage mv "$b" "${b/\/queue\//\/done\/}" >/dev/null 2>&1; done
    done
    echo "idle for ${IDLE} min: powering off"; df -h /; docker system df
    gcloud storage cp /var/log/kel-grade.log "$BASE/results/_workers/$WORKER.log"
    shutdown -h now
    exit 0
fi

# ---- oneshot ----
IDS=$(md kel-ids); PREDS=$(md kel-preds); RUN_ID=$(md kel-run-id); OUT="$BASE/results/$RUN_ID"
gcloud storage rsync -r "$OUT/logs" /work/logs 2>/dev/null || true     # resume from GCS
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
