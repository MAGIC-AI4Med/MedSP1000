#!/usr/bin/env bash
# 清除案例目录中由 z-process-v2 phase1/2/3 生成的所有产物，保留全部原始材料。
#
# 用法：
#   wipe_case_products.sh <case_dir>...           # 清指定 case（可多个）
#   wipe_case_products.sh --all                   # 清 a-case-datas/ 顶层所有 case（排除 discardd/）
#   wipe_case_products.sh -n|--dry-run [...]      # 仅列出会删什么，不真删
#   wipe_case_products.sh -h|--help               # 显示帮助
#
# 安全策略：白名单严格匹配产物文件名 / 目录名，不碰任何其他文件。

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_MULTI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ACASE_DIR="$TEST_MULTI_DIR/a-case-datas"

DRY_RUN=0
USE_ALL=0
CASES=()

usage() {
    sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while (($# > 0)); do
    case "$1" in
        --all)            USE_ALL=1; shift ;;
        -n|--dry-run)     DRY_RUN=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        --)               shift; while (($# > 0)); do CASES+=("$1"); shift; done ;;
        -*)               echo "未知参数: $1" >&2; usage >&2; exit 1 ;;
        *)                CASES+=("$1"); shift ;;
    esac
done

# 模式一：--all 自动收集 a-case-datas/ 顶层 case
if [[ $USE_ALL -eq 1 ]]; then
    if [[ ! -d "$ACASE_DIR" ]]; then
        echo "找不到 a-case-datas 目录: $ACASE_DIR" >&2
        exit 1
    fi
    if [[ ${#CASES[@]} -gt 0 ]]; then
        echo "--all 与显式 case 参数不能同时给" >&2
        exit 1
    fi
    while IFS= read -r d; do CASES+=("$d"); done < <(
        find "$ACASE_DIR" -maxdepth 1 -mindepth 1 -type d ! -name 'discardd' | sort
    )
fi

if [[ ${#CASES[@]} -eq 0 ]]; then
    echo "未指定任何 case_dir，也未使用 --all" >&2
    usage >&2
    exit 1
fi

# 要清除的目录模式
DIR_GLOBS=(scenario*)

# 要清除的精确文件名（glob 已覆盖 phase[0-9]_*，这里只列 glob 不会捕到的老命名）
FILE_NAMES=(
    MD文件总结.md
    md_files_summary.md
)
FILE_GLOBS=(
    'phase[0-9]_*'
    'scenario*_NOT_APPLICABLE.md'
    '*_packets_index.md'
    'generate_*_packets.py'
)

mode_label="WIPE"
[[ $DRY_RUN -eq 1 ]] && mode_label="DRYRUN"

total_dirs=0
total_files=0
processed_cases=0

for case_dir in "${CASES[@]}"; do
    if [[ ! -d "$case_dir" ]]; then
        echo "[$mode_label] SKIP not_a_dir: $case_dir"
        continue
    fi
    processed_cases=$((processed_cases + 1))

    case_dirs_removed=0
    case_files_removed=0

    # 删目录
    for glob in "${DIR_GLOBS[@]}"; do
        # 使用 nullglob 避免无匹配时拿到字面量
        shopt -s nullglob
        for d in "$case_dir"/$glob/; do
            [[ -d "$d" ]] || continue
            d="${d%/}"
            if [[ $DRY_RUN -eq 1 ]]; then
                echo "[DRYRUN] rm -rf $d"
            else
                rm -rf "$d"
            fi
            case_dirs_removed=$((case_dirs_removed + 1))
        done
        shopt -u nullglob
    done

    # 删精确文件名
    for name in "${FILE_NAMES[@]}"; do
        f="$case_dir/$name"
        if [[ -f "$f" ]]; then
            if [[ $DRY_RUN -eq 1 ]]; then
                echo "[DRYRUN] rm $f"
            else
                rm -f "$f"
            fi
            case_files_removed=$((case_files_removed + 1))
        fi
    done

    # 删通配模式
    for glob in "${FILE_GLOBS[@]}"; do
        shopt -s nullglob
        for f in "$case_dir"/$glob; do
            [[ -f "$f" ]] || continue
            if [[ $DRY_RUN -eq 1 ]]; then
                echo "[DRYRUN] rm $f"
            else
                rm -f "$f"
            fi
            case_files_removed=$((case_files_removed + 1))
        done
        shopt -u nullglob
    done

    total_dirs=$((total_dirs + case_dirs_removed))
    total_files=$((total_files + case_files_removed))

    if [[ $case_dirs_removed -gt 0 || $case_files_removed -gt 0 ]]; then
        echo "[$mode_label] $case_dir  dirs=$case_dirs_removed files=$case_files_removed"
    fi
done

echo ""
echo "==================== 汇总 ===================="
echo "处理 case 数:     $processed_cases"
echo "$(printf "%-6s" "$mode_label") scenario 目录: $total_dirs"
echo "$(printf "%-6s" "$mode_label") phase 产物文件: $total_files"
if [[ $DRY_RUN -eq 1 ]]; then
    echo "（dry-run 未真删，去掉 -n 才会执行）"
fi
