#!/usr/bin/env bash

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SIM_ROOT="$ROOT_DIR/a-simulate"

CASE_FILE="$SCRIPT_DIR/scenario_directories_full.json"
CONFIG_FILE="$SIM_ROOT/simulate/config.yaml"
LOG_ROOT_DIR="$ROOT_DIR/z-logs"
LOG_DIR=""
BATCH_NAME="simulate_batch_$(date '+%Y%m%d_%H%M%S')"
BATCH_LOG_FILE=""
PYTHON_BIN="${PYTHON_BIN:-$HOME/miniconda3/envs/mm/bin/python}"
MAX_CONCURRENT="${MAX_CONCURRENT:-2}"
DRY_RUN=0
DEBUG=0
EXAMINEE_MODEL_OVERRIDE=""
SP_ENV_EVAL_MODEL_OVERRIDE=""
EXAMINEE_TTS=""          # off|single|bon|medagents（examinee test-time 策略）
TTS_N=""                 # bon 候选数
TTS_EXPERTS=""           # medagents 专科数
TTS_CONSENSUS_ROUNDS=""  # medagents 共识修订轮数
TTS_SELECTOR=""          # bon 选择器（默认 self_critic）

RESULT_DIR=""

usage() {
    cat <<'EOF'
Usage: run_simulate_cases.sh [options]

Options:
  -j, --jobs N             Number of concurrent jobs. Default: 2
  -c, --case-file PATH     JSON file containing case directories
  -l, --log-dir PATH       Batch log directory. Default: z-logs/simulate_batch_<timestamp>
  -p, --python-bin PATH    Python executable used to run simulate.runner
  --config PATH            Path to simulate/config.yaml
  --examinee-model NAME    Override agents.examinee.model for this batch.
  --sp-env-eval-model NAME Override agents.sp.model / agents.environment.model /
                           agents.evaluator.model with the same NAME for this batch.
                           (status_marker requires the three to match.)
                           If either override is set, a snapshot is written to
                           <log-dir>/config.effective.yaml and used as the
                           effective config for the batch.
  --examinee-tts STRATEGY  Examinee test-time scaling strategy for this batch:
                           off (default, untouched) | single | bon | medagents.
                           Non-off values inject agents.examinee.test_time into
                           config.effective.yaml; status markers land in an
                           isolated `<examinee>__tts-<tag>/` subdir, so previous
                           evaluations are never touched or skipped.
  --tts-n N                bon: number of sampled candidates (default 5)
  --tts-experts N          medagents: number of specialist experts (default 5)
  --tts-consensus-rounds N medagents: consensus revision rounds (default 1)
  --tts-selector NAME      bon selector (default self_critic)
  --debug                  Pass --debug to simulate.runner
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
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --examinee-model)
            EXAMINEE_MODEL_OVERRIDE="$2"
            shift 2
            ;;
        --sp-env-eval-model)
            SP_ENV_EVAL_MODEL_OVERRIDE="$2"
            shift 2
            ;;
        --examinee-tts)
            EXAMINEE_TTS="$2"
            shift 2
            ;;
        --tts-n)
            TTS_N="$2"
            shift 2
            ;;
        --tts-experts)
            TTS_EXPERTS="$2"
            shift 2
            ;;
        --tts-consensus-rounds)
            TTS_CONSENSUS_ROUNDS="$2"
            shift 2
            ;;
        --tts-selector)
            TTS_SELECTOR="$2"
            shift 2
            ;;
        --debug)
            DEBUG=1
            shift
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

# 归一化 / 校验 test-time 策略。空 = off。非法值直接报错，
# 避免误拼写被 Python 端静默回退成 off 而写进历史 marker 目录。
if [[ -z "$EXAMINEE_TTS" ]]; then
    EXAMINEE_TTS="off"
fi
case "$EXAMINEE_TTS" in
    off|single|bon|medagents) ;;
    *)
        echo "Invalid --examinee-tts value: $EXAMINEE_TTS (expected off|single|bon|medagents)" >&2
        exit 1
        ;;
esac

if [[ ! -f "$CASE_FILE" ]]; then
    echo "Case file not found: $CASE_FILE" >&2
    exit 1
fi

if [[ ! -d "$SIM_ROOT" ]]; then
    echo "Simulation root not found: $SIM_ROOT" >&2
    exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Config file not found: $CONFIG_FILE" >&2
    exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    if [[ "$DRY_RUN" -eq 0 ]] || [[ -n "$EXAMINEE_MODEL_OVERRIDE" ]] || [[ -n "$SP_ENV_EVAL_MODEL_OVERRIDE" ]]; then
        echo "Python executable not found or not executable: $PYTHON_BIN" >&2
        exit 1
    fi
fi

if [[ -z "$LOG_DIR" ]]; then
    LOG_DIR="$LOG_ROOT_DIR/$BATCH_NAME"
fi

mkdir -p "$LOG_DIR"
LOG_DIR="$(cd "$LOG_DIR" && pwd)"
RESULT_DIR="$LOG_DIR/.result_state"
mkdir -p "$RESULT_DIR"
BATCH_LOG_FILE="$LOG_DIR/copy.log"
exec > >(tee -a "$BATCH_LOG_FILE") 2>&1

if [[ -n "$EXAMINEE_MODEL_OVERRIDE" ]] || [[ -n "$SP_ENV_EVAL_MODEL_OVERRIDE" ]] || [[ "$EXAMINEE_TTS" != "off" ]]; then
    EFFECTIVE_CONFIG="$LOG_DIR/config.effective.yaml"
    TTS_STRATEGY="$EXAMINEE_TTS" TTS_N="$TTS_N" TTS_EXPERTS="$TTS_EXPERTS" \
    TTS_CONSENSUS_ROUNDS="$TTS_CONSENSUS_ROUNDS" TTS_SELECTOR="$TTS_SELECTOR" \
    "$PYTHON_BIN" - "$CONFIG_FILE" "$EFFECTIVE_CONFIG" "$EXAMINEE_MODEL_OVERRIDE" "$SP_ENV_EVAL_MODEL_OVERRIDE" <<'PY'
import os
import sys
from pathlib import Path

import yaml

src, dst, examinee_model, sp_env_eval_model = sys.argv[1:5]
with open(src, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}
agents = cfg.setdefault("agents", {})

def _role_spec(role: str) -> dict:
    spec = agents.get(role)
    if not isinstance(spec, dict):
        spec = {}
        agents[role] = spec
    return spec

def _set_role_model(role: str, model: str) -> None:
    _role_spec(role)["model"] = model

if examinee_model:
    _set_role_model("examinee", examinee_model)
if sp_env_eval_model:
    for role in ("sp", "environment", "evaluator"):
        _set_role_model(role, sp_env_eval_model)

# test-time 策略：把 agents.examinee.test_time 注入 effective config。
# 只写显式提供的字段，其余沿用 simulate/test_time.py 的默认值。
strategy = (os.environ.get("TTS_STRATEGY") or "off").strip()
if strategy and strategy != "off":
    tt = {"strategy": strategy}
    if os.environ.get("TTS_N"):
        tt["n"] = int(os.environ["TTS_N"])
    if os.environ.get("TTS_EXPERTS"):
        tt["experts"] = int(os.environ["TTS_EXPERTS"])
    if os.environ.get("TTS_CONSENSUS_ROUNDS"):
        tt["consensus_rounds"] = int(os.environ["TTS_CONSENSUS_ROUNDS"])
    if os.environ.get("TTS_SELECTOR"):
        tt["selector"] = os.environ["TTS_SELECTOR"]
    _role_spec("examinee")["test_time"] = tt

Path(dst).write_text(
    yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
PY
    if [[ $? -ne 0 ]] || [[ ! -f "$EFFECTIVE_CONFIG" ]]; then
        echo "Failed to write effective config: $EFFECTIVE_CONFIG" >&2
        exit 1
    fi
    if [[ -n "$EXAMINEE_MODEL_OVERRIDE" ]]; then
        echo "Examinee model override: $EXAMINEE_MODEL_OVERRIDE"
    fi
    if [[ -n "$SP_ENV_EVAL_MODEL_OVERRIDE" ]]; then
        echo "SP/environment/evaluator model override: $SP_ENV_EVAL_MODEL_OVERRIDE"
    fi
    if [[ "$EXAMINEE_TTS" != "off" ]]; then
        echo "Examinee test-time strategy: $EXAMINEE_TTS (n=${TTS_N:-default} experts=${TTS_EXPERTS:-default} consensus_rounds=${TTS_CONSENSUS_ROUNDS:-default} selector=${TTS_SELECTOR:-default})"
    fi
    echo "Effective config: $EFFECTIVE_CONFIG (source: $CONFIG_FILE)"
    CONFIG_FILE="$EFFECTIVE_CONFIG"
fi

STATUS_DIR=$(
    cd "$SIM_ROOT" && "$PYTHON_BIN" -m simulate.status_marker get-status-dir "$CONFIG_FILE" 2>&1
)
STATUS_RC=$?
if [[ "$STATUS_RC" -ne 0 ]]; then
    echo "无法从 $CONFIG_FILE 推导 status 目录（sp/environment/evaluator 模型必须一致）：" >&2
    echo "$STATUS_DIR" >&2
    exit 1
fi
mkdir -p "$STATUS_DIR"

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

make_case_id() {
    local case_dir="$1"
    local base
    base="$(basename "$case_dir")"
    if [[ "$base" == scenario* ]]; then
        printf '%s_%s\n' "$(basename "$(dirname "$case_dir")")" "$base"
    else
        printf '%s\n' "$base"
    fi
}

resolve_case_root() {
    local case_dir="$1"
    if [[ ! -d "$case_dir" ]]; then
        return 1
    fi

    if [[ -d "$case_dir/scenario1" ]]; then
        printf '%s\n' "$case_dir/scenario1"
        return 0
    fi

    printf '%s\n' "$case_dir"
}

marker_filename_for_case_root() {
    local case_root="$1"
    local scenario_id case_id
    scenario_id="$(basename "$case_root")"
    case_id="$(basename "$(dirname "$case_root")")"
    printf '%s_%s.txt\n' "$case_id" "$scenario_id"
}

write_result() {
    local case_id="$1"
    local status="$2"
    local exit_code="$3"
    local case_root="$4"
    local log_file="$5"

    {
        printf 'status=%s\n' "$status"
        printf 'exit_code=%s\n' "$exit_code"
        printf 'case_root=%s\n' "$case_root"
        printf 'log_file=%s\n' "$log_file"
    } > "$RESULT_DIR/${case_id}.result"
}

process_case() {
    local case_dir="$1"
    local case_id
    local case_root
    local log_file
    local marker_name
    local marker_path
    local existing_run_dir
    local rc
    local -a cmd

    case_id="$(make_case_id "$case_dir")"
    log_file="$LOG_DIR/${case_id}.log"

    if ! case_root="$(resolve_case_root "$case_dir")"; then
        echo "[$(timestamp)] FAIL   $case_id reason=case_dir_missing path=$case_dir"
        write_result "$case_id" "failed" "1" "" "$log_file"
        touch "$RESULT_DIR/fail_${case_id}"
        return 1
    fi

    marker_name="$(marker_filename_for_case_root "$case_root")"
    marker_path="$STATUS_DIR/$marker_name"
    if [[ -f "$marker_path" ]]; then
        existing_run_dir="$(head -n 1 "$marker_path" 2>/dev/null)"
        echo "[$(timestamp)] SKIP   $case_id reason=status_marker_exists marker=$marker_path run_dir=$existing_run_dir"
        write_result "$case_id" "skipped" "0" "$case_root" "$log_file"
        touch "$RESULT_DIR/skip_${case_id}"
        return 0
    fi

    cmd=(
        "$PYTHON_BIN"
        -m simulate.runner
        --config "$CONFIG_FILE"
        --case-root "$case_root"
    )

    if [[ "$DEBUG" -eq 1 ]]; then
        cmd+=(--debug)
    fi

    echo "[$(timestamp)] START  $case_id"
    echo "[$(timestamp)] case_dir=$case_dir case_root=$case_root log_file=$log_file"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '[%s] DRYRUN ' "$(timestamp)"
        printf '%q ' "${cmd[@]}"
        printf '\n'
        write_result "$case_id" "dry_run" "0" "$case_root" "$log_file"
        touch "$RESULT_DIR/success_${case_id}"
        return 0
    fi

    (
        cd "$SIM_ROOT" || exit 1
        "${cmd[@]}"
    ) >"$log_file" 2>&1
    rc=$?

    if [[ "$rc" -eq 0 ]]; then
        echo "[$(timestamp)] DONE   $case_id"
        write_result "$case_id" "success" "$rc" "$case_root" "$log_file"
        touch "$RESULT_DIR/success_${case_id}"
    else
        echo "[$(timestamp)] FAIL   $case_id exit_code=$rc log=$log_file"
        write_result "$case_id" "failed" "$rc" "$case_root" "$log_file"
        touch "$RESULT_DIR/fail_${case_id}"
    fi

    return "$rc"
}

echo "Total cases: ${#CASES[@]}"
echo "Concurrency: $MAX_CONCURRENT"
echo "Case file: $CASE_FILE"
echo "Simulation root: $SIM_ROOT"
echo "Config: $CONFIG_FILE"
echo "Status dir: $STATUS_DIR"
echo "Log dir: $LOG_DIR"
echo "Python: $PYTHON_BIN"
echo "Debug: $DEBUG"
echo "================================"

for case_dir in "${CASES[@]}"; do
    process_case "$case_dir" &
    while [ "$(jobs -rp | wc -l)" -ge "$MAX_CONCURRENT" ]; do
        sleep 0.3
    done
done

wait

SUCCESS_COUNT=$(find "$RESULT_DIR" -maxdepth 1 -type f -name 'success_*' | wc -l)
FAIL_COUNT=$(find "$RESULT_DIR" -maxdepth 1 -type f -name 'fail_*' | wc -l)
SKIP_COUNT=$(find "$RESULT_DIR" -maxdepth 1 -type f -name 'skip_*' | wc -l)

SUMMARY_FILE="$LOG_DIR/summary.txt"
{
    echo "Simulation batch summary"
    echo "========================"
    echo "Finished at: $(timestamp)"
    echo "Concurrency: $MAX_CONCURRENT"
    echo "Total cases: ${#CASES[@]}"
    echo "Succeeded: $SUCCESS_COUNT"
    echo "Failed: $FAIL_COUNT"
    echo "Skipped (status marker exists): $SKIP_COUNT"
    echo "Case file: $CASE_FILE"
    echo "Simulation root: $SIM_ROOT"
    echo "Config: $CONFIG_FILE"
    echo "Status dir: $STATUS_DIR"
    echo "Log dir: $LOG_DIR"
    echo
    echo "Case results:"
    for case_dir in "${CASES[@]}"; do
        case_id="$(make_case_id "$case_dir")"
        result_file="$RESULT_DIR/${case_id}.result"
        if [[ -f "$result_file" ]]; then
            echo "[$case_id]"
            cat "$result_file"
            echo
        fi
    done
} > "$SUMMARY_FILE"

echo "================================"
echo "Finished at: $(timestamp)"
echo "Total cases: ${#CASES[@]}"
echo "Succeeded: $SUCCESS_COUNT"
echo "Failed: $FAIL_COUNT"
echo "Skipped: $SKIP_COUNT"
echo "Logs: $LOG_DIR"
echo "Summary: $SUMMARY_FILE"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    exit 1
fi
