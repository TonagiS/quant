import pandas as pd

f = pd.read_csv(r"E:\Code\quant\runs\2026-11-15_single_asset_state\final_summary.csv")
for split in ("train", "test", "all"):
    for g in ("ETF", "长样本"):
        s = f[f.group == g]
        d = s[f"{split}_d"]
        print(split, g, "n=%d" % len(s), "d=%+.3f" % d.median(),
              "正%.0f%%" % (100 * (d > 0).mean()),
              "dfix=%+.3f" % s[f"{split}_dfix"].median(),
              "卡玛=%.3f" % s[f"{split}_calmar"].median(),
              "基准=%.3f" % s[f"{split}_base"].median())
print()
print("全样本 cagr %.2f%% mdd %.2f%% 均仓 %.2f 换手 %.1f" % (
    f.cagr.median() * 100, f.mdd.median() * 100, f.avg_pos.median(), f.turn.median()))
print("all_d 中位 %+.3f all_dfix %+.3f 为正 %.0f%%" % (
    f.all_d.median(), f.all_dfix.median(), 100 * (f.all_d > 0).mean()))
print("持有5只 (HOLDING):")
h = f[f.code.isin(["sh510230", "sh512800", "sh510880", "sz159928", "sh512690"])]
print(h[["code", "test_d", "test_dfix", "cagr", "mdd", "avg_pos", "turn"]].round(3).to_string(index=False))
