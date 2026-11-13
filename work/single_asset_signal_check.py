"""单支定稿策略：把每只标的的"信号拆开"打出来，看加/减仓到底卡在哪一条。

仓位 = 底仓G1 × 波动上限volcap × 趋势衰减taper，三段各自可查：
  G1     : mom20 < 0 → 年线上行(close>MA200 且 MA200 60日斜率>0) 0.5，否则 0；mom20 > +1% 回 1.0
  volcap : clip(0.15/vol60, 0.4, 1.0)
  taper  : T0/T1=1.0  T2=0.85  T3=0.6  T4=0.5

用法：python single_asset_signal_check.py [code ...]
不带参数默认看 POS-01 六只 + 现有持仓。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_final import policy  # noqa: E402
from single_asset_harness import factors, load_universe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ["sh512760", "sh512480", "sh515050", "sh515320", "sh512980", "sz159869"]
HOLDING = ["sh510230", "sh512800", "sh510880", "sz159928", "sh512690"]


def detail(code, df):
    F = factors(df)
    pos, T = policy(F)
    i = -1
    c, ma200 = F["close"].iloc[i], F["ma200"].iloc[i]
    slope = ma200 / F["ma200"].iloc[i - 60] - 1
    mom20, vol60 = F["mom20"].iloc[i], F["vol60"].iloc[i]
    above = c > ma200
    bull = bool(above and slope > 0)
    volcap = min(max(0.15 / vol60, 0.4), 1.0) if vol60 == vol60 else 1.0
    t = int(T.iloc[i])
    taper = {0: 1.0, 1: 1.0, 2: 0.85, 3: 0.6, 4: 0.5}[t]
    g1 = 1.0 if mom20 > 0.01 else (0.5 if (mom20 < 0 and bull) else (0.0 if mom20 < 0 else 1.0))
    return dict(code=code, date=str(df["date"].iloc[-1].date()),
                close=round(c, 3), ma200=round(ma200, 3), slope60=f"{slope:+.2%}",
                above=above, bull=bull, mom20=f"{mom20:+.2%}",
                vol60=f"{vol60:.1%}", volcap=round(volcap, 2),
                T=t, taper=taper, G1=g1,
                target=round(float(pos.iloc[i]), 3),
                prev=round(float(pos.iloc[i - 1]), 3),
                chg=round(float(pos.iloc[i] - pos.iloc[i - 1]), 3))


def main():
    codes = sys.argv[1:] or DEFAULT + HOLDING
    uni = load_universe()
    rows = []
    for code in dict.fromkeys(codes):
        if code not in uni:
            print(f"[跳过] {code} 不在数据池")
            continue
        grp, df = uni[code]
        if len(df) < 300:
            print(f"[跳过] {code} 数据不足 {len(df)}")
            continue
        rows.append(detail(code, df.sort_values("date").reset_index(drop=True)))
    r = pd.DataFrame(rows)
    print(r.to_string(index=False))
    out = ROOT / "runs" / "2026-11-15_single_asset_state" / "signal_detail.csv"
    r.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
