#!/usr/bin/env bash
# 启动 MedGemma-27B vLLM OpenAI-compatible server。
#
# 用法：
#   bash a-simulate/serve_medgemma.sh
#
# 启动后会在终端打印：
#   export MEDGEMMA_BASE_URL=http://<node>:8000/v1
# 把这一行复制到另一个终端的批跑 shell 里执行，再起 run_simulate_cases.sh。
#
# 资源说明：
#   MedGemma-27B bf16 ≈ 54GB；--gres=gpu:1 默认申请单卡，需调度到 ≥80GB 显存的卡。
#   若 medai_p 分到的是 48G 卡，把下面切换到 --gres=gpu:2 + --tensor-parallel-size 2。
#
# 长期运行：vLLM 进程常驻；Ctrl-C 释放 srun 资源；medgemma_base_url 会随节点变化。

set -euo pipefail

MODEL_PATH="${MODEL_PATH:-/mnt/petrelfs/liangcheng/models/medgemma-27b}"
SERVED_NAME="${SERVED_NAME:-medgemma-27b-it}"
PORT="${PORT:-8000}"
PYTHON_BIN="${PYTHON_BIN:-/mnt/petrelfs/liangcheng/miniconda3/envs/mm/bin/python}"

srun -p medai_p --gres=gpu:1 --cpus-per-task=16 --mem=128G \
     --job-name=medgemma_vllm \
     bash -c "
echo '=========================================='
echo \"Node: \$(hostname)\"
echo \"Endpoint: http://\$(hostname):${PORT}/v1\"
echo ''
echo 'In your batch shell run:'
echo \"  export MEDGEMMA_BASE_URL=http://\$(hostname):${PORT}/v1\"
echo '=========================================='
exec '${PYTHON_BIN}' -m vllm.entrypoints.openai.api_server \
    --model '${MODEL_PATH}' \
    --served-model-name '${SERVED_NAME}' \
    --host 0.0.0.0 --port ${PORT} \
    --dtype bfloat16 \
    --max-model-len 8192 \
    --gpu-memory-utilization 0.9 \
    --tensor-parallel-size 1
"
