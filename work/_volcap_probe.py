import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import factors, load_universe  # noqa: E402

CODES = ["sh512760", "sh512480", "sh515050", "sh515320", "sh510880", "sz159928"]
uni = load_universe()
for c in CODES:
    F = factors(uni[c][1])
    v = F["vol60"]
    vc = (0.15 / v).clip(0.4, 1.0)
    m = v.notna()
    w = v.loc["2026-06-22":"2026-07-15"]
    print(c,
          f"vol60 中位 {v[m].median():.1%}", f"最小 {v[m].min():.1%}",
          f"地板占比 {(vc[m] == 0.4).mean():.0%}",
          f"6/22-7/15 均值 {w.mean():.1%}")
