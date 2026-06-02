#!/usr/bin/env bash
# 多 examinee 并发跑批 wrapper（基于 run_simulate_cases.sh）
# sp/env/eval 都用 deepseek-v4-pro (no thinking 由 LLMClient 自动注入)
# 所有模型同时启动，每个模型内部按 -j 并发
# 已跑过的 case 由 status marker 自动 SKIP
#
# 历史名 "_bench_5model_runner.sh"，2026-05-15 加入 medgemma-27b-it 后实际是 6 model；
# 保留旧文件名避免外部引用断链。

set -o pipefail

# 强制走带认证的集群出口代理 proxy_on（<internal-proxy>）。
# 原因：环境里小写 http_proxy/https_proxy 默认指向无认证的 closeai-proxy，
# 会覆盖大写 HTTP_PROXY，导致 httpx(openai SDK) 对走 the aggregator gateway的
# gpt-5.5 / gemini 报 ProxyError 403/503（closeai 禁了该外网聚合站）；
# 而 deepseek/baichuan/qwen 走各自专用域名碰巧能过。此处 4 个变量统一
# 设为认证代理（实测对 deepseek/baichuan/dashscope/the aggregator gateway 全部可达），
# 与 z-process-v2/run_cases.sh 顶部做法一致。详见 goals.md 问题14 / progress.md。
export http_proxy='${HTTP_PROXY:-}'
export https_proxy="$http_proxy"
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$http_proxy"

cd /mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi

# ---- defaults ----
J="${J:-4}"
CASE_FILE="z-process-v2/scenario_directories_intersect_100_padded.json"

usage() {
    cat <<'EOF'
Usage: _bench_5model_runner.sh [options]

Options:
  -j, --jobs N           Per-model concurrency (passed to run_simulate_cases.sh -j).
                         Total in-flight runners ≈ (model count) × N.
                         Also overridable via env var J. CLI > env > default 4.
  -c, --case-file PATH   Case file (scenario-level JSON list).
                         Default: z-process-v2/scenario_directories_intersect_100_padded.json
  -h, --help             Show this help.

Fixed in script (edit the file to change):
  models[]               examinee names (count fixed in script)
  --sp-env-eval-model    deepseek-v4-pro

Logs:
  z-logs/bench_5model_<TS>/<model_slug>/      (per-model run_simulate_cases.sh -l dir)
  z-logs/bench_5model_<TS>/<model_slug>.console.log
EOF
}

while (($# > 0)); do
    case "$1" in
        -j|--jobs)
            J="$2"
            shift 2
            ;;
        -c|--case-file)
            CASE_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if ! [[ "$J" =~ ^[1-9][0-9]*$ ]]; then
    echo "Invalid concurrency: $J" >&2
    exit 1
fi
if [[ ! -f "$CASE_FILE" ]]; then
    echo "Case file not found: $CASE_FILE" >&2
    exit 1
fi

TS=$(date +%Y%m%d_%H%M%S)
ROOT="z-logs/bench_5model_${TS}"
mkdir -p "$ROOT"

models=(
  "gpt-5.5"
  "Baichuan-M3"
  "deepseek-v4-pro"
  "gemini-3.1-pro-preview"
  "qwen3.5-397b-a17b"
  "claude-opus-4-7"
  # medgemma-27b-it 单独走本地 vLLM 全集重跑（run_simulate_cases.sh），不在本 bench 内；
  # 如需重新纳入 bench 取消下行注释（需先起 serve_medgemma_dp4_tp2.sh 并 export MEDGEMMA_BASE_URL）
  # "medgemma-27b-it"
)

# medgemma 走本地 vLLM，需要 MEDGEMMA_BASE_URL 环境变量
needs_medgemma=0
for m in "${models[@]}"; do
  if [[ "$m" == medgemma* ]]; then
    needs_medgemma=1
    break
  fi
done
if (( needs_medgemma )) && [[ -z "${MEDGEMMA_BASE_URL:-}" ]]; then
  echo "ERROR: models[] 含 medgemma 但 MEDGEMMA_BASE_URL 未设置；请先启动 a-simulate/serve_medgemma.sh 并 export MEDGEMMA_BASE_URL=http://<node>:8000/v1" >&2
  exit 1
fi

echo "==== bench_5model_runner ===="
echo "  case_file = $CASE_FILE"
echo "  jobs (J)  = $J  (total runners ≈ $((${#models[@]} * J)))"
echo "  log_root  = $ROOT"
echo "  models    = ${models[*]}"
echo "============================="

pids=()
for m in "${models[@]}"; do
  slug="${m//[^a-zA-Z0-9]/_}"
  LDIR="$ROOT/${slug}"
  echo "==== [$(date '+%H:%M:%S')] LAUNCH examinee=$m  -j=$J  -l=$LDIR ===="
  bash z-process-v2/run_simulate_cases.sh -j "$J" \
      -c "$CASE_FILE" \
      --examinee-model "$m" \
      --sp-env-eval-model deepseek-v4-pro \
      -l "$LDIR" > "$ROOT/${slug}.console.log" 2>&1 &
  pids+=($!)
done

echo "All ${#models[@]} launched. PIDs: ${pids[*]}"
echo "Root: $ROOT"
wait
echo "==== ALL ${#models[@]} MODELS FINISHED at $(date) ===="
for m in "${models[@]}"; do
  slug="${m//[^a-zA-Z0-9]/_}"
  sf="$ROOT/${slug}/summary.txt"
  if [[ -f "$sf" ]]; then
    succ=$(grep -E '^Succeeded:' "$sf" | head -1)
    fail=$(grep -E '^Failed:' "$sf" | head -1)
    skip=$(grep -E '^Skipped' "$sf" | head -1)
    echo "  $m  $succ / $fail / $skip"
  fi
done
