"""
历史数据加载与基础回测组件。
"""
import argparse
import os
import time
from datetime import datetime
import numpy as np
import pandas as pd
import akshare as ak

from macd_structure import ETF_POOL, calc_macd, structure_series

HORIZONS = (5, 10, 20)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data")
DEFAULT_CACHE = os.path.join(DATA_DIR, "cache2019")

def load_hist(code, start, cache_dir=None):
    cache_dir = cache_dir or DEFAULT_CACHE
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{code}.csv")
    today = datetime.now().strftime("%Y%m%d")
    
    if os.path.exists(path):
        mday = time.strftime("%Y%m%d", time.localtime(os.path.getmtime(path)))
        if mday == today:
            try:
                cached = pd.read_csv(path, parse_dates=["date"])
                # 缓存校验：必须有 amount，且不能全为 NaN
                if "amount" in cached.columns and cached["amount"].notna().any():
                    return cached, False
            except Exception:
                pass

    last_err = None
    for _ in range(3):
        try:
            df = ak.stock_zh_a_hist_tx(symbol=code, start_date=start, end_date=today, adjust="qfq")
            if df is None or len(df) < 20:
                raise ValueError("腾讯接口返回空或数据极少")
            
            df["date"] = pd.to_datetime(df["date"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            
            # P0: 绝不造假，没有 amount 就设 NaN
            if "amount" in df.columns:
                df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            elif "volume" in df.columns:
                # 腾讯有时给成交量(手)，乘以收盘价*100作为近似
                df["amount"] = pd.to_numeric(df["volume"], errors="coerce") * df["close"] * 100
            else:
                df["amount"] = np.nan
                
            df = df[["date", "close", "amount"]].dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
            df.to_csv(path, index=False)
            return df, False
        except Exception as e:
            last_err = e
            time.sleep(1.0)
            
    # Fallback: 尝试拉指数
    try:
        idx_code = code.replace("sh", "").replace("sz", "")
        df = ak.stock_zh_index_daily(symbol=f"sh{idx_code}" if code.startswith("sh") else f"sz{idx_code}")
        if df is not None and len(df) > 20:
            df["date"] = pd.to_datetime(df["date"])
            df["amount"] = np.nan
            return df[["date", "close", "amount"]], True
    except Exception:
        pass
        
    raise RuntimeError(f"{code} 拉取失败: {last_err!r}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20190101")
    ap.add_argument("--lookback", type=int, default=120)
    ap.add_argument("--recent", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=120)
    ap.add_argument("--cooldown", type=int, default=10)
    args = ap.parse_args()

    data, failed, is_idx = {}, [], []
    for code, name in ETF_POOL.items():
        try:
            df, is_index = load_hist(code, args.start)
            data[code] = df
            if is_index:
                is_idx.append(name)
        except Exception as e:
            failed.append((name, str(e)[:60]))
            
    print(f"\n=== 数据加载汇报 ===")
    print(f"成功: {len(data)} 只 | 失败: {len(failed)} 只")
    for name, msg in failed:
        print(f"  失败 {name}: {msg}")
    if is_idx:
        print(f"  注意：以下使用指数数据代替ETF：{', '.join(is_idx)}")
        
    if len(data) < 8:
        print("可用数据少于 8 只，终止执行。")
        return

if __name__ == "__main__":
    main()