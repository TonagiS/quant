"""季度确定口径：国家队（汇金/证金/国新/诚通系）十大流通股东持仓，按申万一级行业聚合。

为什么是这个口径
----------------
公开数据里只有「上市公司定期报告披露的十大流通股东」能确定某只股票被谁持有多少股。
ETF 份额、主力资金流都是推断，不放进这份报告。

数据来源
--------
* 股东明细：东方财富 datacenter（akshare: stock_gdfx_free_holding_detail_em）
* 行业归类：申万一级成分（akshare: sw_index_first_info + index_component_sw），缓存到 sw_l1_map.csv

输出
----
data/national_team/quarterly_<date>.csv   逐股明细（只含国家队相关股东）
data/national_team/quarterly_summary.md   行业聚合 + 环比变动

用法
----
venv\\Scripts\\python.exe tools\\nt_quarterly.py 2026-06-30 2026-09-30
"""

from __future__ import annotations

import csv
import io
import sys
import contextlib
import time
from pathlib import Path

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "national_team"
SW_MAP = OUT / "sw_l1_map.csv"
SUMMARY = OUT / "quarterly_summary.md"

# 只认这些明确的国家队主体（含它们委外的单一资管计划）
ENTITIES = [
    "中央汇金投资有限责任公司",
    "中央汇金资产管理有限责任公司",
    "中国证券金融股份有限公司",
    "梧桐树投资平台有限责任公司",
    "凤山投资有限责任公司",
    "国新投资有限公司",
    "中国诚通控股集团有限公司",
    "北京诚通金控投资有限公司",
]
KEY_PATTERN = "|".join(
    [
        "中央汇金",
        "中国证券金融",
        "梧桐树投资",
        "凤山投资",
        "国新投资",
        "诚通",
        "汇金资管单一资产管理计划",
    ]
)


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


def build_sw_map() -> pd.DataFrame:
    if SW_MAP.exists() and (time.time() - SW_MAP.stat().st_mtime) < 90 * 86400:
        return pd.read_csv(SW_MAP, dtype=str)
    l1 = quiet(ak.sw_index_first_info)
    rows = []
    for _, item in l1.iterrows():
        code = str(item["行业代码"]).split(".")[0]
        name = str(item["行业名称"])
        try:
            comp = quiet(ak.index_component_sw, symbol=code)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 申万 {code} {name} 成分拉取失败: {exc}")
            continue
        for _, c in comp.iterrows():
            rows.append({"code": str(c["证券代码"]).zfill(6), "sw_l1": name, "sw_code": code})
    df = pd.DataFrame(rows).drop_duplicates(subset=["code"])
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(SW_MAP, index=False, encoding="utf-8-sig")
    print(f"[ok] 申万映射 {len(df)} 只 -> {SW_MAP.name}")
    return df


def fetch_quarter(report_date: str) -> pd.DataFrame:
    stamp = report_date.replace("-", "")
    df = quiet(ak.stock_gdfx_free_holding_detail_em, date=stamp)
    df = df[df["股东名称"].astype(str).str.contains(KEY_PATTERN, na=False, regex=True)].copy()
    df["期末持股-流通市值"] = pd.to_numeric(df["期末持股-流通市值"], errors="coerce").fillna(0.0)
    df["期末持股-数量"] = pd.to_numeric(df["期末持股-数量"], errors="coerce").fillna(0.0)
    df["期末持股-数量变化"] = pd.to_numeric(df["期末持股-数量变化"], errors="coerce").fillna(0.0)
    df["报告期"] = report_date
    df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
    return df


def main(argv: list[str]) -> None:
    dates = argv or ["2026-06-30"]
    OUT.mkdir(parents=True, exist_ok=True)
    sw = build_sw_map()
    frames = {}
    for d in dates:
        try:
            df = fetch_quarter(d)
        except Exception as exc:  # noqa: BLE001
            print(f"[err] {d} 拉取失败: {exc}")
            continue
        if df.empty:
            print(f"[warn] {d} 无数据")
            continue
        df = df.merge(sw, left_on="股票代码", right_on="code", how="left")
        df["sw_l1"] = df["sw_l1"].fillna("未归类")
        out = OUT / f"quarterly_{d}.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        frames[d] = df
        print(f"[ok] {d}: {len(df)} 条 / {df['股票代码'].nunique()} 只 -> {out.name}")

    if not frames:
        print("[end] 没有可用数据")
        return

    lines = ["# 国家队季度持仓（确定口径）", "", "来源：十大流通股东披露（季报/年报），按申万一级聚合。", ""]
    prev_agg = None
    for d in sorted(frames):
        df = frames[d]
        agg = (
            df.groupby("sw_l1")
            .agg(
                持股市值亿元=("期末持股-流通市值", lambda s: round(s.sum() / 1e8, 1)),
                股票数=("股票代码", "nunique"),
                记录数=("股东名称", "size"),
                本期增持亿股=("期末持股-数量变化", lambda s: round(s.sum() / 1e8, 2)),
            )
            .sort_values("持股市值亿元", ascending=False)
        )
        lines.append(f"## {d}")
        lines.append("")
        lines.append(f"- 合计流通市值 **{agg['持股市值亿元'].sum():.0f} 亿元**，涉及 {df['股票代码'].nunique()} 只股票")
        lines.append("")
        lines.append("| 申万一级 | 市值(亿) | 股票数 | 环比变动(亿) |")
        lines.append("| --- | --- | --- | --- |")
        for name, row in agg.iterrows():
            delta = "-"
            if prev_agg is not None and name in prev_agg.index:
                delta = f"{row['持股市值亿元'] - prev_agg.loc[name, '持股市值亿元']:+.1f}"
            lines.append(f"| {name} | {row['持股市值亿元']:.1f} | {int(row['股票数'])} | {delta} |")
        lines.append("")
        prev_agg = agg

    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] 汇总 -> {SUMMARY.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
