#!/usr/bin/env python3
"""构建 PBOC 政策利率 / 存款准备金率事件表 -> data/macro/pboc_rate_events.csv
来源：东方财富数据中心（转录 PBOC 公告）+ 手工核对的新闻来源。见 runs/2026-11-15_rate_cycle/PREREG.md"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "macro"
OUT.mkdir(parents=True, exist_ok=True)
EM = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName={r}&columns=ALL&pageSize=500&sortColumns=PUBLISH_DATE&sortTypes=-1"
EM_LPR = "https://datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_RATE&sty=ALL&st=TRADE_DATE&sr=-1&p=1&ps=500"
SRC_DEP = "东方财富 RPT_ECONOMY_DEPOSIT_RATE（转录PBOC存贷款基准利率公告）"
SRC_RRR = "东方财富 RPT_ECONOMY_DEPOSIT_RESERVE（转录PBOC降准/升准公告）"
SRC_LPR = "东方财富 RPTA_WEB_RATE（全国银行间同业拆借中心LPR公告）；2026-10-20 LPR 3.0% 经 pbc.gov.cn LPR 页核对"

# 7 天逆回购（2016 起作为政策利率记录）。ann=公告/操作日
OMO = [("2017-02-03", 2.25, 2.35, "叩富网2017债市回顾：OMO 2/3、3/16、12/14 各上调10/10/5bp", ""),
       ("2017-03-16", 2.35, 2.45, "同上", ""),
       ("2017-12-14", 2.45, 2.50, "同上", ""),
       ("2018-03-22", 2.50, 2.55, "财新2019-11-18称上次调整为2018-03-19；记忆为2018-03-22（美联储加息次日）", "uncertain_date(03-19 vs 03-22)"),
       ("2019-11-18", 2.55, 2.50, "财新 2019-11-18；中行债市参考 2019-11-19", ""),
       ("2020-02-03", 2.50, 2.40, "每经 2020-03-30；澎湃", ""),
       ("2020-03-30", 2.40, 2.20, "每经 2020-03-30", ""),
       ("2022-01-17", 2.20, 2.10, "PBOC公开市场公告/CEIC 汇总（搜索核对）", ""),
       ("2022-08-15", 2.10, 2.00, "同上", ""),
       ("2023-06-13", 2.00, 1.90, "每经 2023-06-13", ""),
       ("2023-08-15", 1.90, 1.80, "PBOC公开市场公告/CEIC", ""),
       ("2024-07-22", 1.80, 1.70, "PBOC公开市场公告 [2024]第4号", ""),
       ("2024-09-24", 1.70, 1.50, "潘功胜 2024-09-24 国新办发布会宣布；PBOC 公告[2024]第5号 2024-09-27 执行", "ann=09-24, effective 09-27"),
       ("2025-05-07", 1.50, 1.40, "新华网 2025-05-07 国新办发布会宣布；2025-05-08 执行", "ann=05-07, effective 05-08")]
MLF = [("2017-01-24", 3.00, 3.10, "记忆+2017 债市回顾（MLF 先于 OMO 上调）", "uncertain_level"),
       ("2017-03-16", 3.10, 3.20, "同上", "uncertain_level"),
       ("2017-12-14", 3.20, 3.25, "同上", "uncertain_level"),
       ("2018-03-22", 3.25, 3.30, "同上", "uncertain_level"),
       ("2019-11-05", 3.30, 3.25, "财新 2019-11-18 提及 11/5 下调 MLF", ""),
       ("2020-02-17", 3.25, 3.15, "每经 2020-03-30；上海金融局/PBOC 大事记（搜索核对）", ""),
       ("2020-04-15", 3.15, 2.95, "同上", ""),
       ("2022-01-17", 2.95, 2.85, "同上", ""),
       ("2022-08-15", 2.85, 2.75, "同上", ""),
       ("2023-06-15", 2.75, 2.65, "PBOC 2023 货币政策大事记", ""),
       ("2023-08-15", 2.65, 2.50, "PBOC 2023 货币政策大事记", ""),
       ("2024-07-25", 2.50, 2.30, "上海金融局 2024 货币政策大事记", ""),
       ("2024-09-25", 2.30, 2.00, "上海金融局 2024 货币政策大事记", "")]
# 2006 年升准（东方财富表从 2007 起）
RRR_2006 = [("2006-06-16", 7.5, 8.0), ("2006-07-21", 8.0, 8.5), ("2006-11-03", 8.5, 9.0)]
RRR_ANN_FIX = {"2024-09-27": ("2024-09-24", "潘功胜 2024-09-24 发布会宣布（新华网）"),}


def get(u):
    r = requests.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=40); r.raise_for_status(); return r.json()


def main():
    rows = []
    dep = get(EM.format(r="RPT_ECONOMY_DEPOSIT_RATE"))["result"]["data"]
    for r in dep:
        a = r["PUBLISH_DATE"][:10]; eff = r["REPORT_DATE"][:10]
        if r["DEPOSIT_RATE_BB"] != r["DEPOSIT_RATE_BA"]:
            rows.append(dict(ann_date=a, eff_date=eff, instrument="DEP1Y", old=r["DEPOSIT_RATE_BB"], new=r["DEPOSIT_RATE_BA"], source=SRC_DEP, flag=""))
        if r["LOAN_RATE_SB"] != r["LOAN_RATE_SA"]:
            rows.append(dict(ann_date=a, eff_date=eff, instrument="LOAN1Y", old=r["LOAN_RATE_SB"], new=r["LOAN_RATE_SA"], source=SRC_DEP, flag=""))
    lpr = sorted(get(EM_LPR)["result"]["data"], key=lambda r: r["TRADE_DATE"])
    prev = None
    for r in lpr:
        d, v = r["TRADE_DATE"][:10], r["LPR1Y"]
        if d >= "2019-08-20" and prev is not None and v != prev:
            rows.append(dict(ann_date=d, eff_date=d, instrument="LPR1Y", old=prev, new=v, source=SRC_LPR,
                             flag="reform_first_fixing(旧LPR4.31→新LPR4.25)" if d == "2019-08-20" else ""))
        prev = v
    for a, o, n, s, f in OMO:
        rows.append(dict(ann_date=a, eff_date=a, instrument="OMO7D", old=o, new=n, source=s, flag=f))
    for a, o, n, s, f in MLF:
        rows.append(dict(ann_date=a, eff_date=a, instrument="MLF1Y", old=o, new=n, source=s, flag=f))
    rrr = get(EM.format(r="RPT_ECONOMY_DEPOSIT_RESERVE"))["result"]["data"]
    for a, o, n in RRR_2006:
        rows.append(dict(ann_date=a, eff_date="", instrument="RRR_LARGE", old=o, new=n, source="记忆（东方财富表始于2007）；PBOC 2006 三次上调各0.5pp", flag="uncertain_source"))
    seen = set()
    for r in rrr:
        a = r["REPORT_DATE"][:10]; eff = (r.get("TRADE_DATE_NEW") or "")[:10]
        src, flag = SRC_RRR, ""
        if a in RRR_ANN_FIX:
            a, extra = RRR_ANN_FIX[a]; src += "；" + extra; flag = "ann_moved_earlier"
        b0, b1, s0, s1 = r["INTEREST_RATE_BB"], r["INTEREST_RATE_BA"], r["INTEREST_RATE_SB"], r["INTEREST_RATE_SA"]
        if b0 != b1:
            inst, o, n = "RRR_LARGE", b0, b1
        elif s0 != s1:
            inst, o, n = "RRR_SMALL", s0, s1; flag = (flag + ";" if flag else "") + "small_banks_only"
        else:
            continue
        k = (a, inst, o, n)
        if k in seen:
            continue
        seen.add(k)
        rows.append(dict(ann_date=a, eff_date=eff, instrument=inst, old=o, new=n, source=src, flag=flag))
    df = pd.DataFrame(rows)
    df["direction"] = (df.new - df.old).apply(lambda x: "cut" if x < 0 else "hike")
    df = df.sort_values(["ann_date", "instrument"]).reset_index(drop=True)
    df.rename(columns={"ann_date": "date"}).to_csv(OUT / "pboc_rate_events.csv", index=False, encoding="utf-8-sig")
    print(df.groupby(["instrument", "direction"]).size())
    print(len(df), df.ann_date.min(), df.ann_date.max())


if __name__ == "__main__":
    main()
