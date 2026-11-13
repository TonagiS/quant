"""长池按资产类别拆分（描述性，不据此再拟合参数）。

类别（按名称关键词）：宽基 / 风格(红利·价值·成长·基本面) / 行业主题 / 跨境 / 黄金商品 / 债券
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "2026-11-16_long" / "final"

KW = [
    ("黄金商品", ["黄金", "大宗", "白银", "原油"]),
    ("债券", ["国债", "城投", "信用债", "短融"]),
    ("跨境", ["恒生", "纳指", "标普", "德国", "日经", "中概", "港股"]),
    ("风格", ["红利", "价值", "成长", "基本面", "治理", "责任", "央企", "国企", "超大盘", "产业升级"]),
    ("宽基", ["沪深300", "上证50", "上证180", "中证500", "中证100", "A100", "创业板", "深证100",
              "深成", "中小", "中创", "国证2000", "上证380", "上证中盘", "上证指数", "深300", "深价值"]),
]


def classify(name: str) -> str:
    for cls, kws in KW:
        if any(k in name for k in kws):
            return cls
    return "行业主题"


def main():
    fin = pd.read_csv(RUN / "final_summary.csv")
    uni = pd.read_csv(ROOT / "data" / "etf_universe.csv")
    nm = dict(zip(uni["code"], uni["name"]))
    fin["name"] = fin["code"].map(nm).fillna(fin["code"])
    fin["cls"] = [classify(n) if g == "ETF" else "指数/蓝筹" for n, g in zip(fin["name"], fin["group"])]

    g = fin.groupby("cls").agg(n=("code", "size"), all_d=("all_d", "median"),
                               dfix=("all_dfix", "median"), tr=("train_d", "median"),
                               te=("test_d", "median"), pos=("avg_pos", "median"),
                               cagr=("cagr", "median"), mdd=("mdd", "median")).round(3)
    print(g.to_string())
    print()
    # 每个类别里最好/最差的 3 只
    for cls in g.index:
        sub = fin[fin["cls"] == cls].sort_values("test_d")
        print(f"[{cls}] 最差 test_d: " + ", ".join(f"{r.code}({r.test_d:+.2f})" for r in sub.head(3).itertuples()))
    fin.to_csv(RUN / "final_summary_classified.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
