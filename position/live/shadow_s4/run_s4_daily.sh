#!/usr/bin/env bash
# S4 影子盘每日入口（Linux / bot 的 box）。建议北京时间工作日 15:30 后运行。
# 用法：bash position/live/shadow_s4/run_s4_daily.sh            # 正式
#       DRY=1 bash position/live/shadow_s4/run_s4_daily.sh      # 演练（写 dryrun/）
#       PUSH=1 bash ...                                          # 跑完后只提交 shadow_s4 的日志文件并 fetch+rebase 推送（不 force）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
cd "$ROOT"
export TZ=Asia/Shanghai
PY="${PYTHON:-python3}"
ARGS=()
[[ "${DRY:-0}" == "1" ]] && ARGS+=(--dry-run)
rc=0
{
  echo "=== $(date '+%F %T %Z') run_s4_shadow ${ARGS[*]:-}"
  "$PY" position/live/shadow_s4/run_s4_shadow.py "${ARGS[@]}"
} >> "$HERE/run.log" 2>&1 || rc=$?
tail -n 60 "$HERE/run.log"
echo "exit=$rc"
[[ $rc -ne 0 ]] && exit $rc
if [[ "${PUSH:-0}" == "1" && "${DRY:-0}" != "1" ]]; then
  git add position/live/shadow_s4/s4_log.csv position/live/shadow_s4/arm_*.csv position/live/shadow_s4/obs01_log.csv \
          position/live/shadow_s4/latest.json position/live/shadow_s4/data_overlap_check.csv 2>/dev/null || true
  git add position/live/shadow_s4/weekly_*.md 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git -c user.name="Tonagi quant bot" -c user.email=bot@localhost commit -q -m "shadow_s4: eod log $(date +%F)"
    # 有 GH_TOKEN 时用临时凭据助手（不打印 token）；否则用本机已有的 git 凭据
    CH=()
    [[ -n "${GH_TOKEN:-}" ]] && CH=(-c 'credential.helper=!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f')
    git "${CH[@]}" fetch -q origin && git rebase -q origin/main && git "${CH[@]}" push -q origin HEAD:main
  fi
fi
