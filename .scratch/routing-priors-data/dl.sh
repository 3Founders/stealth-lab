set -u
HF=https://huggingface.co/datasets
get() { mkdir -p "$(dirname "$2")"; [ -s "$2" ] || curl -sSL --retry 3 -o "$2" "$1" && echo "ok $2 $(du -h "$2"|cut -f1)"; }
# SWE-bench experiments: results + metadata only
if [ ! -d experiments ]; then
  git clone -q --filter=blob:none --no-checkout --depth 1 https://github.com/SWE-bench/experiments.git experiments
  git -C experiments sparse-checkout set --no-cone '/evaluation/*/*/results/results.json' '/evaluation/*/*/metadata.yaml' '/evaluation/*/*/README.md'
  git -C experiments checkout -q && echo "ok experiments"
fi
for i in $(seq -w 0 11); do get $HF/nebius/SWE-agent-trajectories/resolve/main/data/train-000${i}-of-00012.parquet swe_agent_traj/train-$i.parquet; done
get $HF/nebius/SWE-rebench-openhands-trajectories/resolve/main/trajectories.parquet openhands/trajectories.parquet
get $HF/nebius/SWE-rebench/resolve/main/data/test-00000-of-00002.parquet swe_rebench/test-0.parquet
get $HF/nebius/SWE-rebench/resolve/main/data/test-00001-of-00002.parquet swe_rebench/test-1.parquet
get $HF/nebius/SWE-rebench-V2/resolve/main/data/train-00000-of-00001.parquet swe_rebench_v2/train.parquet
get $HF/withmartian/routerbench/resolve/main/routerbench_0shot.pkl routerbench/routerbench_0shot.pkl
for m in 2025_01 2025_02 2025_03 2025_04 2025_05 2025_06 2025_07 2025_08 2025_09 2025_10 2025_11 2025_12 2026_01 2026_02 2026_03 test; do get $HF/nebius/SWE-rebench-leaderboard/resolve/main/data/$m-00000-of-00001.parquet rebench_lb/$m.parquet; done
echo ALLDONE
