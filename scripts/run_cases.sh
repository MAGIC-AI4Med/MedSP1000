#!/usr/bin/env bash

set -o pipefail

# Route codex stream through cluster proxy (avoids stream.error reconnect mid-phase)
export http_proxy='${HTTP_PROXY:-}'
export https_proxy="$http_proxy"
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$http_proxy"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

CASE_FILE="$SCRIPT_DIR/case_directories_full.json"
PYTHON_SCRIPT="$ROOT_DIR/z-codex-agent-sdk/test_codex.py"
DEFAULT_PROMPT_FILE="$SCRIPT_DIR/default_prompt.yaml"
JUDGEMENT_DIR="$SCRIPT_DIR/judgement"
LOG_ROOT_DIR="$ROOT_DIR/z-logs"
LOG_DIR=""
BATCH_NAME="codex_batch_$(date '+%Y%m%d_%H%M%S')"
BATCH_LOG_FILE=""
PYTHON_BIN="${PYTHON_BIN:-$HOME/miniconda3/envs/mm/bin/python}"
MAX_CONCURRENT="${MAX_CONCURRENT:-4}"
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: run_cases.sh [options]

Options:
  -j, --jobs N             Number of concurrent jobs. Default: 4
  -c, --case-file PATH     JSON file containing case directories
  -l, --log-dir PATH       Batch log directory. Default: z-logs/codex_batch_<timestamp>
  -p, --python-bin PATH    Python executable to run test_codex.py
  --default-prompt PATH    Fallback prompt file when a case has no prompt.yaml
  --dry-run                Print commands without executing them
  -h, --help               Show this help message
EOF
}

while (($# > 0)); do
    case "$1" in
        -j|--jobs)
            MAX_CONCURRENT="$2"
            shift 2
            ;;
        -c|--case-file)
            CASE_FILE="$2"
            shift 2
            ;;
        -l|--log-dir)
            LOG_DIR="$2"
            shift 2
            ;;
        -p|--python-bin)
            PYTHON_BIN="$2"
            shift 2
            ;;
        --default-prompt)
            DEFAULT_PROMPT_FILE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
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

if ! [[ "$MAX_CONCURRENT" =~ ^[1-9][0-9]*$ ]]; then
    echo "Invalid concurrency value: $MAX_CONCURRENT" >&2
    exit 1
fi

if [[ ! -f "$CASE_FILE" ]]; then
    echo "Case file not found: $CASE_FILE" >&2
    exit 1
fi

if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    echo "Python script not found: $PYTHON_SCRIPT" >&2
    exit 1
fi

if [[ ! -f "$DEFAULT_PROMPT_FILE" ]]; then
    echo "Default prompt file not found: $DEFAULT_PROMPT_FILE" >&2
    exit 1
fi

if [[ "$DRY_RUN" -eq 0 ]] && [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python executable not found or not executable: $PYTHON_BIN" >&2
    exit 1
fi

if [[ -z "$LOG_DIR" ]]; then
    LOG_DIR="$LOG_ROOT_DIR/$BATCH_NAME"
fi

mkdir -p "$LOG_DIR"
mkdir -p "$JUDGEMENT_DIR"
BATCH_LOG_FILE="$LOG_DIR/copy.log"
exec > >(tee -a "$BATCH_LOG_FILE") 2>&1

mapfile -t CASES < <(
    python3 - "$CASE_FILE" <<'PY'
import json
import sys
from pathlib import Path

case_file = Path(sys.argv[1])
data = json.loads(case_file.read_text(encoding="utf-8"))
if not isinstance(data, list):
    raise SystemExit("case_directories.json must be a JSON array")
for item in data:
    if isinstance(item, str) and item.strip():
        print(item.strip())
PY
)

if [[ "${#CASES[@]}" -eq 0 ]]; then
    echo "No cases found in $CASE_FILE" >&2
    exit 1
fi

timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

case_prompt_file() {
    local case_dir="$1"
    local prompt_file="$case_dir/prompt.yaml"
    if [[ -f "$prompt_file" ]]; then
        printf '%s\n' "$prompt_file"
    else
        printf '%s\n' "$DEFAULT_PROMPT_FILE"
    fi
}

process_case() {
    local case_dir="$1"
    local case_id
    local prompt_file
    local log_file
    local rc

    case_id="$(basename "$case_dir")"
    prompt_file="$(case_prompt_file "$case_dir")"
    log_file="$LOG_DIR/${case_id}.log"

    echo "[$(timestamp)] START  $case_id"
    echo "[$(timestamp)] case_dir=$case_dir prompt_file=$prompt_file log_file=$log_file"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[$(timestamp)] DRYRUN $PYTHON_BIN $PYTHON_SCRIPT --working-directory '$case_dir' --prompt-file '$prompt_file'"
        return 0
    fi

    "$PYTHON_BIN" "$PYTHON_SCRIPT" \
        --working-directory "$case_dir" \
        --prompt-file "$prompt_file" \
        >"$log_file" 2>&1
    rc=$?

    if [[ "$rc" -eq 0 ]]; then
        touch "$JUDGEMENT_DIR/${case_id}.txt"
        echo "[$(timestamp)] DONE   $case_id"
    else
        echo "[$(timestamp)] FAIL   $case_id exit_code=$rc log=$log_file"
    fi

    return "$rc"
}

declare -a RUNNING_PIDS=()
declare -A PID_TO_CASE=()
COMPLETED=0
SUCCEEDED=0
FAILED=0
SKIPPED=0

launch_case() {
    local case_dir="$1"
    process_case "$case_dir" &
    local pid=$!
    RUNNING_PIDS+=("$pid")
    PID_TO_CASE["$pid"]="$case_dir"
}

reap_one() {
    local i
    local pid
    local rc
    local case_dir
    local case_id

    while true; do
        for i in "${!RUNNING_PIDS[@]}"; do
            pid="${RUNNING_PIDS[$i]}"
            if ! kill -0 "$pid" 2>/dev/null; then
                case_dir="${PID_TO_CASE[$pid]}"
                case_id="$(basename "$case_dir")"

                wait "$pid"
                rc=$?

                unset "RUNNING_PIDS[$i]"
                RUNNING_PIDS=("${RUNNING_PIDS[@]}")
                unset "PID_TO_CASE[$pid]"

                COMPLETED=$((COMPLETED + 1))
                if [[ "$rc" -eq 0 ]]; then
                    SUCCEEDED=$((SUCCEEDED + 1))
                else
                    FAILED=$((FAILED + 1))
                fi

                echo "[$(timestamp)] PROGRESS completed=$COMPLETED succeeded=$SUCCEEDED failed=$FAILED running=${#RUNNING_PIDS[@]} last=$case_id"
                return 0
            fi
        done
        sleep 1
    done
}

echo "Total cases: ${#CASES[@]}"
echo "Concurrency: $MAX_CONCURRENT"
echo "Case file: $CASE_FILE"
echo "Log dir: $LOG_DIR"
echo "Python: $PYTHON_BIN"
echo "Script: $PYTHON_SCRIPT"
echo "================================"

for case_dir in "${CASES[@]}"; do
    case_id="$(basename "$case_dir")"
    if [[ -f "$JUDGEMENT_DIR/${case_id}.txt" ]]; then
        SKIPPED=$((SKIPPED + 1))
        COMPLETED=$((COMPLETED + 1))
        echo "[$(timestamp)] SKIP   $case_id reason=marker_exists"
        echo "[$(timestamp)] PROGRESS completed=$COMPLETED succeeded=$SUCCEEDED failed=$FAILED skipped=$SKIPPED running=${#RUNNING_PIDS[@]} last=$case_id"
        continue
    fi

    bash "$SCRIPT_DIR/wipe_case_products.sh" "$case_dir" >/dev/null 2>&1 || true

    while [[ "${#RUNNING_PIDS[@]}" -ge "$MAX_CONCURRENT" ]]; do
        reap_one
    done
    launch_case "$case_dir"
done

while [[ "${#RUNNING_PIDS[@]}" -gt 0 ]]; do
    reap_one
done

echo "================================"
echo "Finished at: $(timestamp)"
echo "Total cases: ${#CASES[@]}"
echo "Succeeded: $SUCCEEDED"
echo "Failed: $FAILED"
echo "Skipped: $SKIPPED"
echo "Logs: $LOG_DIR"

if [[ "$FAILED" -gt 0 ]]; then
    exit 1
fi
