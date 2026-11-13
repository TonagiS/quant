"""TREND-01 登记：写 registry.csv 与 indicators/trend/prereg/registry_trend.csv（先登记后运行）。"""
import csv
import datetime
import hashlib
from pathlib import Path

R = Path(r"E:\Code\quant")
SCRIPT = R / "indicators/trend/code/trend01_prereg.py"
deps = ["indicators/code/backtest_composite.py", "indicators/code/backtest_structure.py",
        "indicators/code/macd_structure.py", "indicators/code/pools.py"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


dep_hash = hashlib.sha256("".join(sha(R / d) for d in deps).encode()).hexdigest()[:16]
code_hash = hashlib.sha256((sha(SCRIPT) + dep_hash).encode()).hexdigest()[:16]
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

row = {
    "id": "TREND-01", "登记时间": now,
    "假设": "趋势转换信号（V-A MA60方向 / V-B 40日突破）的事件方向成立，且替换 S2 的 MA60 过滤后卡玛提升、成本不脆",
    "宇宙": "base;ext（ETF，start=20190101，快照 2026-11-02）",
    "冻结参数": "V-A: close vs MA60 且 MA60 vs 5日前；V-B: close vs 前40日高/低；事件20日内去重；h=5/10/20；k5 reb5 delay1 cost5bp；状态机 底部后允许/顶部后清仓",
    "判据": "C1 20日超额 |NW_t|>=3 且方向符合；C2 卡玛>=S2+0.05且回撤不差于S2-2pp；C3 盈亏平衡>=30bp且换手<=1.5x S2；C4 delay1/2 卡玛均>=S2；C5 2019-2022与2023-2026卡玛差同号；两池同时满足",
    "运行命令": "python indicators/trend/code/trend01_prereg.py（cwd=runs/2026-11-05_TREND-01）",
    "结果": "", "判定": "",
    "备注": "试验数 2；无 git 仓库，用 SHA256+时间戳代替 commit；不碰 decision_1430、shadow v1/v2",
    "code_sha256": code_hash, "试验数": "2",
}

p = R / "registry.csv"
rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
fields = list(rows[0].keys())
rows.append(row)
with open(p, "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
print("root registry +1", code_hash)

p2 = R / "indicators/trend/prereg/registry_trend.csv"
rows2 = list(csv.DictReader(open(p2, encoding="utf-8-sig"))) if p2.exists() else []
f2 = ["id", "登记时间", "侧别", "假设", "宇宙", "冻结参数", "判据", "运行命令",
      "结果", "判定", "备注", "code_sha256", "试验数"]
r2 = {"id": "TREND-01", "登记时间": now, "侧别": "up+down（V-A/V-B 各含顶/底）",
      "假设": row["假设"], "宇宙": row["宇宙"], "冻结参数": row["冻结参数"], "判据": row["判据"],
      "运行命令": row["运行命令"], "结果": "", "判定": "", "备注": row["备注"],
      "code_sha256": code_hash, "试验数": "2"}
rows2.append(r2)
with open(p2, "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, fieldnames=f2)
    w.writeheader()
    w.writerows(rows2)
print("trend registry +1", now)
