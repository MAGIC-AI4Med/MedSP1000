#!/usr/bin/env bash
# 用冻结 rubric 对所有 judge=deepseek-v4-pro 的已有 run 重打分（只判 true/false，不重走模拟）。
#
# 非侵入：每个 run 目录只新增 final_evaluation_frozen_rubric.json，原 final_evaluation.json /
# agent_logs 一字不动。判分器固定 deepseek-v4-pro（与原 eval 同模型，把"冻结 rubric"隔离成唯一变量）。
#
# 续跑安全：
#   - 已存在且为合法 JSON 的结果 -> 跳过；存在但损坏（被杀进程截断等）-> 自动 --force 重跑。
#   - controller 端原子写（.tmp + os.replace），最终文件不会出现半截损坏。
#   - 缺冻结 rubric -> 记 skip 跳过。rubric 仍可能并发补全，本脚本随时重跑补齐。
#
# 日志：每次运行一个独立自包含目录 z-rubric/logs/run_<TS>/（互不污染；没失败就没有 fail.txt）：
#   meta.txt  运行描述(起止时间/耗时/参数/计划数/命令/主机/结果)
#   progress.log  流式逐条进度(tail -f 看这个)
#   ok.txt    成功明细，含生成的 final_evaluation_frozen_rubric.json 绝对路径(直接去看)
#   fail.txt / skip_no_rubric.txt / skip_other.txt / bad_json_rerun.txt  仅在有内容时创建
#   summary.txt  收尾统计 + 按 examinee 的均值完成率 / 0项 run 数
#   z-rubric/logs/latest -> run_<TS>  符号链接，tail -f z-rubric/logs/latest/progress.log
#
# 用法（CLI 参数优先；环境变量仍可用作默认值，便于裸跑）:
#   bash z-rubric/rescore_all_frozen_rubric.sh                       # 全量，默认 4 并发
#   bash z-rubric/rescore_all_frozen_rubric.sh -j 64                 # 调并发（真并行，非串行）
#   bash z-rubric/rescore_all_frozen_rubric.sh -n 20 --sample        # 跨 examinee 随机抽 20 条
#   bash z-rubric/rescore_all_frozen_rubric.sh --timeout 240 --force # 单 case 超时 240s，覆盖已有
#
#   # 提交 slurm（推荐）：srun 不解析 VAR=val 前缀，必须用 -j/-n 这类脚本参数
#   srunc bash z-rubric/rescore_all_frozen_rubric.sh -j 64
#
# 参数 / 等价环境变量:
#   -j N        NPROC(4)     并发（真并行 LLM 请求数）
#   -n N        LIMIT(0)     只跑前 N 条；0=全量
#   --sample    SAMPLE(0)    遍历前跨 examinee 随机抽样
#   --timeout N TIMEOUT(180) 单 case 墙钟超时秒；0=不限
#   --force     FORCE(0)     覆盖已有结果（含合法的）
#   -h          帮助
set -uo pipefail

ROOT=/mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi/a-simulate
TM=/mnt/petrelfs/liangcheng/data/simulate_eval/scrapy_meded/test_multi
PY=/mnt/petrelfs/liangcheng/miniconda3/envs/mm/bin/python
RUBDIR="$TM/z-rubric/rubrics"
STATUS_ROOT="$ROOT/status/deepseek-v4-pro"
LOGDIR="$TM/z-rubric/logs"

# 环境变量作默认值；下面 CLI 参数解析会覆盖（srun 不解析 VAR=val 前缀，故必须支持参数）。
NPROC="${NPROC:-4}"
FORCE="${FORCE:-0}"
LIMIT="${LIMIT:-0}"        # 0 = 全量
SAMPLE="${SAMPLE:-0}"      # 1 = 遍历前随机打散（跨 examinee 取代表性样本）
TIMEOUT="${TIMEOUT:-180}"  # 单 case 墙钟超时秒数；0 = 不限

_usage() { sed -n '2,36p' "$0"; exit "${1:-0}"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    -j|--jobs)    NPROC="${2:?-j 需要数值}"; shift 2 ;;
    -n|--limit)   LIMIT="${2:?-n 需要数值}"; shift 2 ;;
    --timeout)    TIMEOUT="${2:?--timeout 需要数值}"; shift 2 ;;
    --sample)     SAMPLE=1; shift ;;
    --force)      FORCE=1; shift ;;
    -h|--help)    _usage 0 ;;
    --)           shift; break ;;
    *) echo "未知参数: $1（-h 看用法）" >&2; exit 2 ;;
  esac
done
for _v in NPROC LIMIT TIMEOUT; do
  if ! [[ "${!_v}" =~ ^[0-9]+$ ]]; then
    echo "参数错误: $_v='${!_v}' 必须是非负整数" >&2; exit 2
  fi
done

TS=$(date +%Y%m%d_%H%M%S)
RUNDIR="$LOGDIR/run_$TS"          # 本次运行独立自包含目录
mkdir -p "$RUNDIR"
ln -sfn "run_$TS" "$LOGDIR/latest"
META="$RUNDIR/meta.txt"
PROG="$RUNDIR/progress.log"
OK_F="$RUNDIR/ok.txt"
FAIL_F="$RUNDIR/fail.txt"
NORUB_F="$RUNDIR/skip_no_rubric.txt"
OTHER_F="$RUNDIR/skip_other.txt"
BADJ_F="$RUNDIR/bad_json_rerun.txt"
START=$(date +%s)
cd "$ROOT"

# 先把待处理标记物化成稳定列表（精确计划数 + SAMPLE 抽样固定 + LIMIT 截断）。
LIST=$(mktemp)
trap 'rm -f "$LIST"' EXIT
find "$STATUS_ROOT" -mindepth 2 -maxdepth 2 -name '*.txt' | sort > "$LIST"
[[ "$SAMPLE" == "1" ]] && shuf "$LIST" -o "$LIST"
if [[ "$LIMIT" =~ ^[0-9]+$ && "$LIMIT" -gt 0 ]]; then
  head -n "$LIMIT" "$LIST" > "$LIST.cut" && mv "$LIST.cut" "$LIST"
fi
PLANNED=$(wc -l < "$LIST")

{
  echo "run_id      $TS"
  echo "started     $(date '+%Y-%m-%d %H:%M:%S')"
  echo "params      NPROC=$NPROC LIMIT=$LIMIT SAMPLE=$SAMPLE TIMEOUT=$TIMEOUT FORCE=$FORCE"
  echo "planned     $PLANNED   (judge=deepseek-v4-pro 树)"
  echo "command     bash z-rubric/rescore_all_frozen_rubric.sh -j $NPROC -n $LIMIT --timeout $TIMEOUT$([[ $SAMPLE == 1 ]] && echo ' --sample')$([[ $FORCE == 1 ]] && echo ' --force')"
  echo "host        $(hostname)"
  echo "run_dir     $RUNDIR"
} > "$META"

cat <<BANNER
=========================================================
 冻结 rubric 重打分
   计划遍历 : $PLANNED 个标记（judge=deepseek-v4-pro 树）
   并发     : NPROC=$NPROC  （真并行：同时最多 $NPROC 个 LLM 请求）
   单 case 超时: $([[ "$TIMEOUT" -gt 0 ]] && echo "${TIMEOUT}s" || echo "不限")
   抽样     : SAMPLE=$SAMPLE   截断 LIMIT=$LIMIT   覆盖 FORCE=$FORCE
   日志目录 : $RUNDIR
   实时进度 : tail -f $LOGDIR/latest/progress.log
=========================================================
BANNER

rescore_one() {
  local marker="$1"
  local base examinee run_dir rub out force_flag rc out_json
  local status out_path comp tot ratio
  base=$(basename "$marker" .txt)              # <case>_<scenario>
  examinee=$(basename "$(dirname "$marker")")  # status/deepseek-v4-pro/<examinee>/<base>.txt
  run_dir=$(cat "$marker")
  rub="$RUBDIR/$base.json"
  if [[ ! -f "$rub" ]]; then
    printf '%s\t%s\n' "$examinee" "$base" >> "$NORUB_F"
    echo "[skip-no-rubric] $examinee $base"
    return 0
  fi
  out="$run_dir/final_evaluation_frozen_rubric.json"
  force_flag=""
  if [[ "$FORCE" == "1" ]]; then
    force_flag="--force"
  elif [[ -f "$out" ]]; then
    # 续跑双保险：存在且合法 JSON -> 真跳过（不重复调 LLM）；损坏 -> --force 重跑覆盖。
    if "$PY" -c 'import json,sys; json.load(open(sys.argv[1]))' "$out" 2>/dev/null; then
      return 0
    fi
    printf '%s\t%s\n' "$examinee" "$base" >> "$BADJ_F"
    echo "[bad-json->rerun] $examinee $base"
    force_flag="--force"
  fi
  local -a run_cmd
  run_cmd=("$PY" -m simulate.runner --config simulate/config.yaml
           --eval-from-run-dir "$run_dir" --rubric-file "$rub"
           --eval-model deepseek-v4-pro)
  [[ -n "$force_flag" ]] && run_cmd+=("$force_flag")
  if [[ "$TIMEOUT" =~ ^[0-9]+$ && "$TIMEOUT" -gt 0 ]]; then
    run_cmd=(timeout "$TIMEOUT" "${run_cmd[@]}")
  fi
  out_json=$("${run_cmd[@]}" 2>/dev/null); rc=$?
  # runner 的 stdout 混有 ConsoleLogger/evaluate 噪声，summary 以 @@RESCORE_JSON@@ 单行打出；
  # 只取最后一条哨兵行解析。ok 含 out_path/completed_items/total_items；skip 含 status。
  # 解析失败/非零退出 -> 失败。Tab 分隔避免路径含空格出错。
  IFS=$'\t' read -r status out_path comp tot < <(
    printf '%s' "$out_json" | "$PY" -c '
import json,sys
SEN="@@RESCORE_JSON@@ "
last=None
for ln in sys.stdin:
    if ln.startswith(SEN): last=ln[len(SEN):]
try:
    d=json.loads(last)
    print("\t".join([str(d.get("status","?")), str(d.get("out_path","-")),
                      str(d.get("completed_items",-1)), str(d.get("total_items",-1))]))
except Exception:
    print("PARSEERR\t-\t-1\t-1")
' 2>/dev/null)
  status="${status:-PARSEERR}"
  if [[ "$rc" -eq 0 && "$status" == "ok" ]]; then
    ratio=$(awk -v c="$comp" -v t="$tot" 'BEGIN{ if(t+0>0) printf "%.2f", c/t; else printf "NA" }')
    printf '%s\t%s\tdone=%s/%s\tr=%s\t%s\n' "$examinee" "$base" "$comp" "$tot" "$ratio" "$out_path" >> "$OK_F"
    echo "[ok] $examinee $base  $comp/$tot r=$ratio  -> $out_path"
  elif [[ "$rc" -eq 0 && "$status" == skipped_* ]]; then
    printf '%s\t%s\t%s\n' "$examinee" "$base" "$status" >> "$OTHER_F"
    echo "[skip-$status] $examinee $base"
  else
    printf '%s\t%s\t%s\trc=%s\tstatus=%s\n' "$examinee" "$base" "$run_dir" "$rc" "$status" >> "$FAIL_F"
    echo "[FAIL rc=$rc status=$status] $examinee $base -> $run_dir"   # rc=124=超时
  fi
}
export -f rescore_one
export RUBDIR PY FORCE TIMEOUT OK_F FAIL_F NORUB_F OTHER_F BADJ_F

# 每条结果一行 -> 终端 + progress.log（tee 单写者，并行不撕行）；结构化明细由 worker 写各 .txt。
xargs -a "$LIST" -P "$NPROC" -I{} bash -c 'rescore_one "$@"' _ {} \
  2>&1 | tee -a "$PROG"

cnt() { [[ -f "$1" ]] && wc -l < "$1" || echo 0; }
ok=$(cnt "$OK_F"); fail=$(cnt "$FAIL_F"); norub=$(cnt "$NORUB_F")
other=$(cnt "$OTHER_F"); badj=$(cnt "$BADJ_F")
skipped_done=$(( PLANNED - ok - fail - norub - other ))
DUR=$(( $(date +%s) - START ))

{
  echo "===== 收尾统计 run $TS ====="
  echo "计划 $PLANNED ｜ 成功 $ok ｜ 失败 $fail ｜ 缺rubric $norub ｜ 其他skip $other ｜ 已完成跳过 ~$skipped_done ｜ 修复损坏 $badj"
  echo "耗时 ${DUR}s"
  if [[ -f "$OK_F" ]]; then
    echo
    echo "--- 按 examinee 的均值完成率（来自 ok.txt 本次成功项）---"
    awk -F'\t' '{
      r=$4; sub(/^r=/,"",r); if(r=="NA"){z[$1]++; next}
      s[$1]+=r; n[$1]++
    } END{
      for(e in n) printf "  %-26s mean_ratio=%.3f  n=%d  零项run=%d\n", e, s[e]/n[e], n[e], (e in z?z[e]:0)
      for(e in z) if(!(e in n)) printf "  %-26s mean_ratio=NA     n=0  零项run=%d\n", e, z[e]
    }' "$OK_F" | sort
    zero=$(awk -F'\t' '$4=="r=NA"' "$OK_F" | wc -l)
    echo "  （零项 run = 冻结 rubric 本身无评分项，下游分析需带规模 caveat，见 goals.md）总零项 run: $zero"
  fi
} | tee "$RUNDIR/summary.txt"

{
  echo "finished    $(date '+%Y-%m-%d %H:%M:%S')   (${DUR}s)"
  echo "result      ok=$ok fail=$fail no_rubric=$norub skip_other=$other skipped_done=$skipped_done bad_json_fixed=$badj"
} >> "$META"

cat <<TAIL
=========================================================
DONE. 计划 $PLANNED ｜ 成功 $ok ｜ 失败 $fail ｜ 缺rubric $norub ｜ 已完成跳过 ~$skipped_done ｜ 修复损坏 $badj
  日志目录 : $RUNDIR
  成功明细 : $OK_F   （每行末尾即生成的 JSON 路径，可直接打开查看）
  $( [[ -f "$FAIL_F" ]] && echo "失败明细 : $FAIL_F" || echo "失败明细 : 无失败（未创建 fail.txt）" )
  收尾汇总 : $RUNDIR/summary.txt
  只重跑失败：复跑本命令即可（成功且合法 JSON 的自动跳过；幂等）
=========================================================
TAIL
