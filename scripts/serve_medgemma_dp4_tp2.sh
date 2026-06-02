#!/usr/bin/env bash
# 启动 4 个 MedGemma-27B vLLM 实例（每个 TP=2）在同一 8-GPU 节点上：
#   实例 0 → GPU 0,1 → 端口 8000
#   实例 1 → GPU 2,3 → 端口 8001
#   实例 2 → GPU 4,5 → 端口 8002
#   实例 3 → GPU 6,7 → 端口 8003
#
# 总 GPU 占用：8 张 A100/H100；4 个 vLLM 独立批处理 → 几乎线性 4× 吞吐；
# 每个 TP=2 释放 ~50G KV cache → max_model_len 可拉到 16384。
#
# 启动后会打印：
#   export MEDGEMMA_BASE_URL=http://<node>:8000/v1,http://<node>:8001/v1,...
# LLMClient 已支持逗号分隔多端点 round-robin。
#
# 用法：bash a-simulate/serve_medgemma_dp4_tp2.sh
# 中止：scancel <jobid> 或 Ctrl-C；4 个 vLLM 子进程会随 srun 一起退出。

set -euo pipefail

MODEL_PATH="${MODEL_PATH:-/mnt/petrelfs/liangcheng/models/medgemma-27b}"
SERVED_NAME="${SERVED_NAME:-medgemma-27b-it}"
BASE_PORT="${BASE_PORT:-8000}"
N_INSTANCES="${N_INSTANCES:-4}"
TP="${TP:-2}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"
PYTHON_BIN="${PYTHON_BIN:-/mnt/petrelfs/liangcheng/miniconda3/envs/mm/bin/python}"

NEED_GPUS=$(( N_INSTANCES * TP ))
LOG_DIR="${LOG_DIR:-/mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi/z-logs/medgemma_dp${N_INSTANCES}_tp${TP}_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$LOG_DIR"

srun -p medai_p --gres=gpu:${NEED_GPUS} --cpus-per-task=64 --mem=384G \
     --nodes=1 --ntasks=1 \
     --job-name=medgemma_dp${N_INSTANCES}_tp${TP} \
     bash -c "
set -e
HOST=\$(hostname)
echo '=========================================='
echo \"Node: \$HOST\"
echo \"Instances: ${N_INSTANCES}  TP per instance: ${TP}  Total GPUs: ${NEED_GPUS}\"
echo \"Ports: ${BASE_PORT}-\$((${BASE_PORT}+${N_INSTANCES}-1))\"
URLS=\$(for i in \$(seq 0 \$((${N_INSTANCES}-1))); do echo -n \"http://\$HOST:\$((${BASE_PORT}+i))/v1\"; if [ \$i -lt \$((${N_INSTANCES}-1)) ]; then echo -n ','; fi; done)
echo
echo 'When all 4 are ready (look for 4× \"Application startup complete\"), run in batch shell:'
echo \"  export MEDGEMMA_BASE_URL=\$URLS\"
echo \"  export NO_PROXY=\$HOST,localhost,127.0.0.1\"
echo '=========================================='

LOG_DIR='${LOG_DIR}'
for i in \$(seq 0 \$((${N_INSTANCES}-1))); do
    GPU_START=\$((i * ${TP}))
    GPU_END=\$((GPU_START + ${TP} - 1))
    DEVICES=\$(seq -s, \$GPU_START \$GPU_END)
    PORT=\$((${BASE_PORT} + i))
    echo \"[\$(date +%H:%M:%S)] launching instance #\$i on GPUs=\$DEVICES port=\$PORT log=\$LOG_DIR/vllm_\$i.log\"
    # 关键：每实例独立 cache root，避免 4 个进程 race condition：
    # - VLLM_CACHE_ROOT / TORCHINDUCTOR_CACHE_DIR: torch.compile 缓存
    # - TRITON_CACHE_DIR: triton kernel 缓存
    # - TORCH_EXTENSIONS_DIR: torch.utils.cpp_extension JIT 构建路径（xgrammar 的 .so）
    INST_CACHE=\"\$LOG_DIR/cache_\$i\"
    mkdir -p \"\$INST_CACHE/vllm\" \"\$INST_CACHE/inductor\" \"\$INST_CACHE/triton\" \"\$INST_CACHE/torch_ext\"
    CUDA_VISIBLE_DEVICES=\"\$DEVICES\" \
    VLLM_CACHE_ROOT=\"\$INST_CACHE/vllm\" \
    TORCHINDUCTOR_CACHE_DIR=\"\$INST_CACHE/inductor\" \
    TRITON_CACHE_DIR=\"\$INST_CACHE/triton\" \
    TORCH_EXTENSIONS_DIR=\"\$INST_CACHE/torch_ext\" \
        '${PYTHON_BIN}' -m vllm.entrypoints.openai.api_server \
        --model '${MODEL_PATH}' \
        --served-model-name '${SERVED_NAME}' \
        --host 0.0.0.0 --port \$PORT \
        --dtype bfloat16 \
        --max-model-len ${MAX_MODEL_LEN} \
        --gpu-memory-utilization 0.9 \
        --tensor-parallel-size ${TP} \
        --guided-decoding-backend outlines \
        > \"\$LOG_DIR/vllm_\$i.log\" 2>&1 &
done

echo \"All ${N_INSTANCES} vLLM launched in parallel inside srun. Tail logs to track readiness:\"
for i in \$(seq 0 \$((${N_INSTANCES}-1))); do
    echo \"  tail -F \$LOG_DIR/vllm_\$i.log\"
done
wait
"
