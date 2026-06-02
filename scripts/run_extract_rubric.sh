#!/usr/bin/env bash
#
# Batch driver for the rubric-extraction pipeline.
#
# Forked from z-stat-case-tags/code/run_classify.sh. Key differences:
#   - reads ../scenario_list.json (a copy of
#     z-process-v2/scenario_directories_full.json: 1639 scenario dirs)
#   - case id is SCENARIO-level: <parent_dir_name>_<scenario_dir_name>
#     (e.g. mededportal_10011_scenario2), because bare basename "scenario1"
#     collides across cases
#   - calls code/run_codex_extract_rubric.py (single-phase)
#   - skips a scenario iff ../rubrics/<case_id>_<scenario>.json already exists
#   - logs to ../logs/extract_rubric_batch_<ts>/
#
# This pipeline ONLY reads each scenario's evaluator/ materials and writes
# JSON into ../rubrics/. It never modifies a-simulate, existing runs, or the
# scenario directories.

set -o pipefail

# Cluster proxy — codex stream needs this on petrelfs (see memory note
# "codex 跑批走集群代理"; same as z-stat-case-tags/code/run_classify.sh).
export http_proxy='${HTTP_PROXY:-}'
export https_proxy="$http_proxy"
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$http_proxy"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

CASE_FILE="$PIPELINE_DIR/scenario_list.json"
PYTHON_SCRIPT="$SCRIPT_DIR/run_codex_extract_rubric.py"
PROMPT_FILE="$SCRIPT_DIR/extract_rubric_prompt.yaml"
PROMPT_KEY="judgment"
RUBRICS_DIR="$PIPELINE_DIR/rubrics"
LOG_ROOT_DIR="$PIPELINE_DIR/logs"
LOG_DIR=""
BATCH_NAME="extract_rubric_batch_$(date '+%Y%m%d_%H%M%S')"
BATCH_LOG_FILE=""
PYTHON_BIN="${PYTHON_BIN:-/mnt/petrelfs/liangcheng/miniconda3/envs/mm/bin/python}"
MAX_CONCURRENT="${MAX_CONCURRENT:-4}"
DRY_RUN=0
FORCE=0
LIMIT=0

usage() {
    cat <<'EOF'
Usage: run_extract_rubric.sh [options]

Options:
  -j, --jobs N             Number of concurrent jobs. Default: 4
  -c, --case-file PATH     JSON array of scenario directories
                           (default: <pipeline>/scenario_list.json)
  -l, --log-dir PATH       Batch log directory
                           (default: <pipeline>/logs/extract_rubric_batch_<ts>)
  -p, --python-bin PATH    Python executable for run_codex_extract_rubric.py
  --prompt-file PATH       YAML prompt file
                           (default: <code>/extract_rubric_prompt.yaml)
  --prompt-key NAME        Prompt key inside the YAML (default: judgment)
  -n, --limit N            Process at most N scenarios from the list (0=all)
  --force                  Re-run scenarios even if rubric file exists
  --dry-run                Print commands without executing
  -h, --help               Show this help message
EOF
}

while (($# > 0)); do
    case "$1" in
        -j|--jobs)         MAX_CONCURRENT="$2"; shift 2 ;;
        -c|--case-file)    CASE_FILE="$2"; shift 2 ;;
        -l|--log-dir)      LOG_DIR="$2"; shift 2 ;;
        -p|--python-bin)   PYTHON_BIN="$2"; shift 2 ;;
        --prompt-file)     PROMPT_FILE="$2"; shift 2 ;;
        --prompt-key)      PROMPT_KEY="$2"; shift 2 ;;
        -n|--limit)        LIMIT="$2"; shift 2 ;;
        --force)           FORCE=1; shift ;;
        --dry-run)         DRY_RUN=1; shift ;;
        -h|--help)         usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

if ! [[ "$MAX_CONCURRENT" =~ ^[1-9][0-9]*$ ]]; then
    echo "Invalid concurrency: $MAX_CONCURRENT" >&2; exit 1
fi
if ! [[ "$LIMIT" =~ ^[0-9]+$ ]]; then
    echo "Invalid limit: $LIMIT" >&2; exit 1
fi
if [[ ! -f "$CASE_FILE" ]]; then
    echo "Case file not found: $CASE_FILE" >&2; exit 1
fi
if [[ ! -f "$PYTHON_SCRIPT" ]]; then
    echo "Python script not found: $PYTHON_SCRIPT" >&2; exit 1
fi
if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "Prompt file not found: $PROMPT_FILE" >&2; exit 1
fi
if [[ "$DRY_RUN" -eq 0 ]] && [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python executable not found: $PYTHON_BIN" >&2; exit 1
fi

mkdir -p "$RUBRICS_DIR"
if [[ -z "$LOG_DIR" ]]; then
    LOG_DIR="$LOG_ROOT_DIR/$BATCH_NAME"
fi
mkdir -p "$LOG_DIR"
BATCH_LOG_FILE="$LOG_DIR/batch.log"
exec > >(tee -a "$BATCH_LOG_FILE") 2>&1

mapfile -t CASES < <(
    python3 - "$CASE_FILE" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(data, list):
    raise SystemExit("scenario_list.json must be a JSON array")
for item in data:
    if isinstance(item, str) and item.strip():
        print(item.strip())
PY
)

if [[ "${#CASES[@]}" -eq 0 ]]; then
    echo "No scenarios found in $CASE_FILE" >&2; exit 1
fi

if [[ "$LIMIT" -gt 0 ]]; then
    CASES=("${CASES[@]:0:$LIMIT}")
fi

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }

# Scenario-level id: <parent_dir_name>_<scenario_dir_name>
rubric_id() {
    local scenario_dir="$1"
    local scenario parent
    scenario="$(basename "$scenario_dir")"
    parent="$(basename "$(dirname "$scenario_dir")")"
    echo "${parent}_${scenario}"
}

process_case() {
    local case_dir="$1"
    local case_id
    local log_file
    local rc

    case_id="$(rubric_id "$case_dir")"
    log_file="$LOG_DIR/${case_id}.log"

    echo "[$(timestamp)] START  $case_id"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[$(timestamp)] DRYRUN $PYTHON_BIN $PYTHON_SCRIPT \\"
        echo "         --working-directory '$case_dir' \\"
        echo "         --prompt-file '$PROMPT_FILE' \\"
        echo "         --prompt-key '$PROMPT_KEY'"
        return 0
    fi

    "$PYTHON_BIN" "$PYTHON_SCRIPT" \
        --working-directory "$case_dir" \
        --prompt-file "$PROMPT_FILE" \
        --prompt-key "$PROMPT_KEY" \
        >"$log_file" 2>&1
    rc=$?

    # codex sometimes raises stream.error AFTER writing the json (e.g. during
    # post-write verification). Treat "json exists" as success regardless of rc.
    if [[ -f "$RUBRICS_DIR/${case_id}.json" ]]; then
        if [[ "$rc" -eq 0 ]]; then
            echo "[$(timestamp)] DONE   $case_id"
        else
            echo "[$(timestamp)] DONE   $case_id (codex rc=$rc but json was written; treating as success; log=$log_file)"
            rc=0
        fi
    else
        if [[ "$rc" -eq 0 ]]; then
            echo "[$(timestamp)] WARN   $case_id codex returned 0 but no rubric json produced; log=$log_file"
            rc=2
        else
            echo "[$(timestamp)] FAIL   $case_id exit_code=$rc log=$log_file"
        fi
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
    local i pid rc case_dir case_id
    while true; do
        for i in "${!RUNNING_PIDS[@]}"; do
            pid="${RUNNING_PIDS[$i]}"
            if ! kill -0 "$pid" 2>/dev/null; then
                case_dir="${PID_TO_CASE[$pid]}"
                case_id="$(rubric_id "$case_dir")"
                wait "$pid"; rc=$?
                unset "RUNNING_PIDS[$i]"
                RUNNING_PIDS=("${RUNNING_PIDS[@]}")
                unset "PID_TO_CASE[$pid]"
                COMPLETED=$((COMPLETED + 1))
                if [[ "$rc" -eq 0 ]]; then
                    SUCCEEDED=$((SUCCEEDED + 1))
                else
                    FAILED=$((FAILED + 1))
                fi
                echo "[$(timestamp)] PROGRESS completed=$COMPLETED succeeded=$SUCCEEDED failed=$FAILED skipped=$SKIPPED running=${#RUNNING_PIDS[@]} last=$case_id"
                return 0
            fi
        done
        sleep 1
    done
}

echo "Total scenarios: ${#CASES[@]}"
echo "Concurrency: $MAX_CONCURRENT"
echo "Case file: $CASE_FILE"
echo "Rubrics dir: $RUBRICS_DIR"
echo "Log dir: $LOG_DIR"
echo "Python: $PYTHON_BIN"
echo "Script: $PYTHON_SCRIPT"
echo "Prompt: $PROMPT_FILE (key=$PROMPT_KEY)"
echo "Force re-run: $FORCE"
echo "================================"

for case_dir in "${CASES[@]}"; do
    case_id="$(rubric_id "$case_dir")"
    if [[ "$FORCE" -eq 0 && -f "$RUBRICS_DIR/${case_id}.json" ]]; then
        SKIPPED=$((SKIPPED + 1))
        COMPLETED=$((COMPLETED + 1))
        echo "[$(timestamp)] SKIP   $case_id reason=rubric_exists"
        echo "[$(timestamp)] PROGRESS completed=$COMPLETED succeeded=$SUCCEEDED failed=$FAILED skipped=$SKIPPED running=${#RUNNING_PIDS[@]} last=$case_id"
        continue
    fi
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
echo "Total scenarios: ${#CASES[@]}"
echo "Succeeded: $SUCCEEDED"
echo "Failed: $FAILED"
echo "Skipped: $SKIPPED"
echo "Logs: $LOG_DIR"

if [[ "$FAILED" -gt 0 ]]; then
    exit 1
fi
