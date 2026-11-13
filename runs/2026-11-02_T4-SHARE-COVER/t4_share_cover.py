"""T4：沪市 ETF 份额数据可行性（只查数据，不测因子）。"""
import json
import os
import sys

import pandas as pd

ROOT = r"E:\Code\quant"
RUN = os.path.join(ROOT, "runs", "2026-11-02_T4-SHARE-COVER")
os.makedirs(RUN, exist_ok=True)

uni = pd.read_csv(os.path.join(ROOT, "shadow", "universe_v1.csv"), dtype={"code": str})
sh = [c for c in uni.loc[uni["status"] == "OK", "code"] if c.startswith("sh")]
sz = [c for c in uni.loc[uni["status"] == "OK", "code"] if c.startswith("sz")]
close_idx = pd.read_csv(os.path.join(ROOT, "work", "cache2019", "sh510300.csv"), parse_dates=["date"])["date"]
print(f"ext 池：沪市 {len(sh)} 只，深市 {len(sz)} 只；样本日期取 1/4/7/10 月最后一个交易日")

dates = []
for y in range(2019, 2027):
    for m in (1, 4, 7, 10):
        s = close_idx[(close_idx.dt.year == y) & (close_idx.dt.month == m)]
        if len(s) and s.max() <= pd.Timestamp("2026-10-31"):
            dates.append(s.max())
dates = sorted(set(dates))
print(f"样本日期 {len(dates)} 个：{dates[0].date()} ~ {dates[-1].date()}")

import akshare as ak  # noqa: E402

rows, cover = [], {c: 0 for c in sh}
for d in dates:
    ds = d.strftime("%Y%m%d")
    try:
        df = ak.fund_etf_scale_sse(date=ds)
        codes = set(df["基金代码"].astype(str).str.zfill(6))
        hit = [c for c in sh if c[2:] in codes]
        for c in hit:
            cover[c] += 1
        rows.append({"样本日": ds, "沪市ETF总数": len(df), "池内沪市命中": len(hit),
                     "池内沪市总数": len(sh), "覆盖率": len(hit) / len(sh),
                     "缺失代码": ",".join(c for c in sh if c[2:] not in codes)})
        print(f"  {ds}: 全市场 {len(df)} 只，命中 {len(hit)}/{len(sh)}")
    except Exception as e:
        rows.append({"样本日": ds, "沪市ETF总数": 0, "池内沪市命中": 0, "池内沪市总数": len(sh),
                     "覆盖率": 0.0, "缺失代码": f"FETCH_FAIL {type(e).__name__}: {str(e)[:60]}"})
        print(f"  {ds}: FAIL {type(e).__name__}")

cov = pd.DataFrame(rows)
cov.to_csv(os.path.join(RUN, "share_cover.csv"), index=False, encoding="utf-8-sig")
code_cov = pd.DataFrame({"代码": sh, "命中样本日数": [cover[c] for c in sh]})
code_cov["覆盖率"] = code_cov["命中样本日数"] / len(dates)
code_cov.to_csv(os.path.join(RUN, "share_code_coverage.csv"), index=False, encoding="utf-8-sig")

sz_note = {"深市可用源": "未找到", "尝试1": "ak.fund_etf_scale_szse() -> TypeError(bytes)",
           "尝试2": "东财系接口(fund_etf_spot_em 等)被墙", "结论": "深市份额需另找源，暂不纳入"}
with open(os.path.join(RUN, "deep_market_note.json"), "w", encoding="utf-8") as fp:
    json.dump(sz_note, fp, indent=2, ensure_ascii=False)

print(f"\n沪市覆盖：平均 {cov['覆盖率'].mean():.1%}，最低 {cov['覆盖率'].min():.1%}，失败日 {int((cov['沪市ETF总数']==0).sum())}")
full = code_cov[code_cov["覆盖率"] >= 0.99]
print(f"全样本日都有数据的沪市标的：{len(full)}/{len(sh)}；覆盖率<50% 的：{int((code_cov['覆盖率']<0.5).sum())}")
print(code_cov.sort_values("覆盖率").head(5).to_string(index=False))
print("深市：" + sz_note["结论"])
