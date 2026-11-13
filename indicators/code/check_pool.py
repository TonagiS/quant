"""
检查标的池能否拉到数据、历史多长、上市后成交额缺失多少。
用法：  python check_pool.py --pool ext            # 默认
        python check_pool.py --pool long+stocks --start 20050101
输出 pool_check.csv。拉不到的代码会标记 FAIL，回测时会自动跳过，不影响运行。
"""
import argparse
import os

import pandas as pd

from macd_structure import ETF_POOL
from pools import get_pool, load_one

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data")


def main():
    try:  # GBK 控制台打印 ✓ 会崩：不可编码字符替换成 ?，不改变原编码
        import sys
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="ext")
    ap.add_argument("--start", default="20050101")
    a = ap.parse_args()

    cache_dir = os.path.join(DATA_DIR, "cache2007" if a.start <= "20070101" else "cache2019")
    print(f"缓存目录: {cache_dir}")
    rows = []
    pool = get_pool(a.pool, ETF_POOL)
    for code, (name, kind) in pool.items():
        try:
            df, is_idx = load_one(code, kind, a.start, cache_dir)
            df = df.sort_values("date")
            first, last = df["date"].iloc[0], df["date"].iloc[-1]
            miss = float(df["amount"].isna().mean() * 100) if "amount" in df.columns else 100.0
            rows.append({"代码": code, "名称": name, "类型": kind + ("(指数替代)" if is_idx else ""), "状态": "OK",
                         "首日": first.date(), "末日": last.date(), "交易日数": len(df),
                         "年数": round((last - first).days / 365.25, 1), "上市后成交额缺失%": round(miss, 1), "备注": ""})
        except Exception as e:
            rows.append({"代码": code, "名称": name, "类型": kind, "状态": "FAIL", "首日": "", "末日": "",
                         "交易日数": 0, "年数": 0, "上市后成交额缺失%": "", "备注": str(e)[:80]})
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 300)
    print(df.to_string(index=False))
    ok = df[df["状态"] == "OK"]
    print(f"\n可用 {len(ok)}/{len(df)} 只；≥10年: {(ok['年数'] >= 10).sum()} 只；≥15年: {(ok['年数'] >= 15).sum()} 只；"
          f"成交额缺失>5%: {(pd.to_numeric(ok['上市后成交额缺失%'], errors='coerce') > 5).sum()} 只")
    df.to_csv("pool_check.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
