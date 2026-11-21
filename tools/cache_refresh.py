"""收盘后刷新行情缓存（每晚跑，幂等）。

- 池内标的（data/cache2019 + data/cache2007，91 只）→ 重写 data/cache_ohlc/<code>.csv，
  并续写 cache2019 / cache2007 里的长历史文件（口径对齐用重叠段中位比值）。
- 池外关注板块（single_asset_daily.WATCH 里不在 ETF 池的两只）→ 只刷 data/cache_ohlc_full/<code>.csv。

为什么要有这个：2026-11-16 发现 cache_ohlc 停在 11-13、cache_ohlc_full 停在 11-13，
单支规则拿旧价算 T 档，播报把"创新药已清仓"说成刚触发。数据自己不更新，规则再对也白搭。

用法：
  python cache_refresh.py              # 池内 + 池外，全部
  python cache_refresh.py --pool-only  # 只刷池内（快）
  python cache_refresh.py --extra-only # 只刷池外两只
失败以非 0 退出，供计划任务/通知捕获。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pull_ohlcv as P  # noqa: E402

FULL = P.DATA / "cache_ohlc_full"
EXTRA = ["sh588200", "sz159865"]      # 科创芯片 / 猪肉养殖：不在 ETF 池，只有全市场缓存
COLS = ["date", "open", "high", "low", "close", "volume", "amount"]
TDIR = ("cache2019", "cache2007")


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def fetch(code: str, kind: str):
    if kind == "etf":
        return P.fetch_etf_tx(code)
    if kind == "index":
        return P.fetch_index(code)
    return P.fetch_stock(code)


def refresh_one(code: str, kind: str, old_path: Path | None, out_path: Path, long_path: Path | None):
    df, src = fetch(code, kind)
    if len(df) < 200:
        raise RuntimeError(f"rows={len(df)}")
    df = P.merge_old_amount(df, old_path if old_path else out_path)
    df[COLS].to_csv(out_path, index=False, float_format="%.6g")
    added, ratio = P.refresh_cache(df, long_path if long_path else out_path)
    return dict(code=code, kind=kind, source=src, rows=len(df), end=str(df["date"].iloc[-1].date()),
                added=added, ratio=round(ratio, 4))


def main() -> int:
    argv = set(sys.argv[1:])
    pool_only, extra_only = "--pool-only" in argv, "--extra-only" in argv
    meta, fails, warns = [], [], []

    if not extra_only:
        files = []
        for d in TDIR:
            files += sorted((P.DATA / d).glob("*.csv"))
        log(f"池内标的 {len(files)}")
        for i, p in enumerate(files, 1):
            code, kind, _ = P.classify(p.stem)
            try:
                m = refresh_one(code, kind, p, P.OUT / f"{code}.csv", p)
                meta.append(m)
                log(f"[{i:02d}/{len(files)}] {code:<8} {m['source']:<10} {m['rows']:5d}行 "
                    f"{m['end']} +{m['added']}")
            except Exception as e:  # noqa: BLE001
                fails.append((code, repr(e)))
                log(f"[{i:02d}/{len(files)}] {code:<8} FAIL {e!r}")
            time.sleep(P.SLEEP)

    if not pool_only:
        log(f"池外关注板块 {len(EXTRA)}")
        time.sleep(30)         # 刚打完 90 个请求，腾讯会短时 501，先歇口气
        for code in EXTRA:
            out = FULL / f"{code}.csv"
            m = None
            for attempt in (1, 2):
                try:
                    m = refresh_one(code, "etf", out, out, out)
                    break
                except Exception as e:  # noqa: BLE001
                    log(f"  {code:<8} 第{attempt}次失败 {e!r}")
                    time.sleep(30)
            if m is None:
                # 兜底换新浪（若可用），仍失败只记警告，不让整条缓存刷新挂掉
                try:
                    m = refresh_one(code, "stock", out, out, out)
                    log(f"  {code:<8} 改用新浪兜底成功")
                except Exception as e:  # noqa: BLE001
                    warns.append((code, repr(e)))
                    log(f"  {code:<8} 全部源失败，保留旧缓存 {e!r}")
                    continue
            meta.append(m)
            log(f"  {code:<8} {m['source']:<10} {m['rows']:5d}行 {m['end']} +{m['added']}")
            time.sleep(P.SLEEP)

    if meta:
        pd.DataFrame(meta).to_csv(P.OUT / "_meta.csv", index=False, encoding="utf-8-sig")
    log(f"完成：成功 {len(meta)}，失败 {len(fails)}，警告 {len(warns)}")
    for c, e in fails:
        log(f"  FAIL {c}: {e}")
    for c, e in warns:
        log(f"  WARN {c}: {e}")

    # 自查：池内缓存最新日期是不是今天（交易日）。不是就提一句，方便第二天早上看日志。
    today = time.strftime("%Y-%m-%d")
    fp = P.OUT / "sh512800.csv"
    if fp.exists():
        last = str(pd.read_csv(fp)["date"].iloc[-1])[:10]
        log(f"自查 sh512800 缓存末日 {last}（今天 {today}）" + ("" if last >= today else " ← 腾讯今日日线还没出，等下次跑"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

