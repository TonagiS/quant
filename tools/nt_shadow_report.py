"""影子运行报告：只统计 shadow_signal_log.csv，不发任何交易指令。

用法：
    venv\\Scripts\\python.exe tools\\nt_shadow_report.py            # 打印
    venv\\Scripts\\python.exe tools\\nt_shadow_report.py --notify   # 打印并发 QQ 通知
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "national_team" / "shadow_signal_log.csv"
SHARES = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
INDEX = ROOT / "data" / "national_team" / "index_daily.csv"
PRICE = ROOT / "data" / "cache2019" / "sh510300.csv"
NOTIFY = Path(r"E:\Code\dsh-agent\notify_event.ps1")


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def pct(a: float, b: float) -> str:
    return f"{(b / a - 1) * 100:+.2f}%" if a else "-"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args()

    log = read_csv(LOG)
    if not log:
        print("[warn] 影子日志为空")
        return

    dates = sorted({r["data_date"] for r in log})
    sig_rows = [r for r in log if r["signal"] == "1"]
    def_rows = [r for r in log if r["state"] == "防守"]
    cur = log[-1]

    idx = {r["date"]: float(r["close"]) for r in read_csv(INDEX) if r["code"] == "sh000001"}
    px = {r["date"]: float(r["close"]) for r in read_csv(PRICE)}
    d0, d1 = dates[0], dates[-1]

    lines = [
        "影子运行报告（国家队份额防守信号）",
        f"- 记录 {len(dates)} 个交易日：{d0} ~ {d1}",
        f"- 触发信号 {len(sig_rows)} 次 / 处于防守窗口 {len(def_rows)} 个交易日",
        f"- 最新：{cur['data_date']} cum14={cur['cum14']}% 状态={cur['state']}",
    ]
    if d0 in idx and d1 in idx:
        lines.append(f"- 同期上证 {idx[d0]:.0f} → {idx[d1]:.0f}（{pct(idx[d0], idx[d1])}）；"
                     f"510300 {pct(px.get(d0, 0), px.get(d1, 0))}")
    if sig_rows:
        for r in sig_rows[:5]:
            lines.append(f"  · {r['data_date']} 信号=1 cum14={r['cum14']}% 防守自 {r['defense_from']} 至 {r['defense_until']}")
    else:
        lines.append("- 影子期内未触发（阈值 cum14 ≤ -10%）")
    lines.append("- 只记录，不交易；参数已冻结，不再追加测试")

    text = "\n".join(lines)
    print(text)

    if args.notify and NOTIFY.exists():
        import subprocess

        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(NOTIFY),
             "-Kind", "done", "-Text", text.replace("\n", "；")[:900]],
            check=False,
        )
        print("[ok] 已发通知")


if __name__ == "__main__":
    main()
