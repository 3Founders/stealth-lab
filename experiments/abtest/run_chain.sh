#!/usr/bin/env bash
# Phase 1 arm C, then phase 2 arm A, then phase 2 arm C. Exit 3 (Claude usage limit): wait 10 min and resume.
# Exit 4 (General Compute nearly out of tokens for the day): stop the whole chain -- resume by hand after its reset.
cd "$(dirname "$0")" || exit 1
until curl -s -o /dev/null http://127.0.0.1:8765/; do sleep 2; done
EXCL="batch1,batch2,batch3,batch4,tf-sonnet,smoke-hooksonly,smoke-phase1"
C_FLAGS="--parallel --testfirst-model claude-sonnet-5-5 --gc-key-name general_compute_api_key1"

step() {   # name, log, runner args...
  local name=$1 log=$2; shift 2
  for i in $(seq 1 60); do
    echo "=== $name attempt $i $(date -u) ===" >> "$log"
    python -u runner.py "$@" >> "$log" 2>&1
    rc=$?
    echo "=== $name exit $rc $(date -u) ===" >> "$log"
    if [ $rc -eq 3 ]; then sleep 600; continue; fi
    return $rc
  done
  return 3
}

step "phase1 C" .local/phase1.out --run phase1 --seed 20261010 --tasks ds1000:30,bird:50,ifeval:20,boolq:10,openapi:20 \
  --arms C $C_FLAGS --exclude-runs $EXCL || { echo "CHAIN STOPPED after phase1 C (rc=$?)"; exit 1; }
step "phase2 A" .local/phase2.out --run phase2 --seed 20261010 --tasks multiple_js:20,d2c:30,websight:25 \
  --arms A || { echo "CHAIN STOPPED after phase2 A (rc=$?)"; exit 1; }
step "phase2 C" .local/phase2.out --run phase2 --seed 20261010 --tasks multiple_js:20,d2c:30,websight:25 \
  --arms C $C_FLAGS || { echo "CHAIN STOPPED after phase2 C (rc=$?)"; exit 1; }
echo "CHAIN DONE"
