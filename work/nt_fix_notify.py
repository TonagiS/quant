# -*- coding: utf-8 -*-
"""一次性补发：汇金播报更正（份额抓到 11-16，替换 14:43 那条滞后的）。"""
import sys
from datetime import datetime

sys.path.insert(0, r"E:\Code\quant\tools")
import nt_brief as nb  # noqa: E402

sh = nb.load_shares()
tot = sh.sum(axis=1)
latest = tot.index[-1]
i = len(tot) - 1
c3 = (tot.iloc[i] - tot.iloc[i - 3]) / tot.iloc[i - 3] * 100
st = nb.rule_state(nb.cum14_series())
flow = nb.fetch_flow()
rc = float(st["cum14"])
dstr = latest.strftime("%m-%d")
ts = datetime.now().strftime("%m-%d %H:%M")

text = "\n".join([
    "【更正】上一条汇金那条抓得早了一天，份额只到 11-13；这条是 11-16 的，以这条为准：",
    "",
    f"{ts}｜主力 {flow['main']:+.0f} 亿",
    "",
    "国家队（汇金）",
    f"3日(至 {dstr}) {c3:+.2f}%",
    "14日(至 %s) %+.2f%% %s -10%%，%s" % (
        dstr, rc, "≤" if rc <= -10 else "＞",
        "⚠️触发减仓规则" if st["signal"] else "未触发减仓规则"),
])
print(text)
nb.notify(text, str(nb.group_cfg().get("group", "")))
