#!/usr/bin/env python3
"""全市场 A 股 ETF：定稿 G1×volcap×taper 与预登记 3 态规则评估。

规则与过滤在 runs/2026-11-14_fullmkt_single_asset/PREREG.md 写死后再跑。
用法：
  python work/fullmkt_single_asset.py universe
  python work/fullmkt_single_asset.py pull
  python work/fullmkt_single_asset.py eval
  python work/fullmkt_single_asset.py all
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "2026-11-14_fullmkt_single_asset"
CACHE = ROOT / "data" / "cache_ohlc_full"
RUN.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
PULL_START = "2017-01-01"
PULL_END = "2026-12-31"
EVAL_START = "2019-01-01"
TRAIN_END = "2022-12-31"
TEST_START = "2023-01-01"
TD = 252
COST = 0.0005
MIN_BARS_CROSS = 250
MIN_BARS_STATS = 60
MIN_MA200_VALID = 60
MIN_AVG_AMOUNT = 5_000_000  # RMB, last 60d
AMOUNT_SCALE = 100  # Tencent volume is in lots (100 shares)
K_VOL, FLOOR = 0.15, 0.4
TAPER_BASE = {0: 1.0, 1: 1.0, 2: 0.85, 3: 0.6, 4: 0.5}
TAPER_3S = {0: 1.0, 1: 1.0, 2: 0.5, 3: 0.5, 4: 0.5}
SLEEP = 0.15
WORKERS = 4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import hyst, run_pos, stats  # noqa: E402


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


# --------------- official category + sector tags ---------------
SECTOR_RULES: list[tuple[str, list[str]]] = [
    ("半导体设备", ["半导体设备", "半导体材料设备"]),
    ("芯片/半导体", ["科创芯片", "芯片设计", "芯片", "半导体"]),
    ("机器人", ["机器人"]),
    ("银行", ["银行"]),
    ("养殖/畜牧", ["畜牧", "养殖", "生猪", "猪肉", "养猪"]),
    ("传媒", ["传媒", "影视", "文娱传媒", "游戏传媒"]),
    ("游戏", ["游戏", "动漫"]),
    ("通信", ["通信", "5G"]),
    ("计算机", ["计算机", "软件", "信创", "云计算", "数据要素", "大数据",
              "网络安全", "信息安全", "工业互联网", "信息技术"]),
    ("人工智能", ["人工智能", "算力"]),
    ("证券", ["证券", "券商"]),
    ("保险", ["保险"]),
    ("房地产", ["房地产", "地产"]),
    ("医药生物", ["医药", "医疗", "生物", "中药", "创新药", "疫苗", "器械", "CXO"]),
    ("新能源车", ["新能源车", "智能汽车", "智能驾驶", "汽车零部件", "汽车"]),
    ("光伏", ["光伏"]),
    ("风电", ["风电"]),
    ("电池/储能", ["电池", "储能", "锂电"]),
    ("新能源", ["新能源", "碳中和", "氢能", "绿色电力"]),
    ("电力", ["电力", "电网", "特高压", "电力设备"]),
    ("军工", ["军工", "国防", "航空航天", "航空", "航天"]),
    ("有色金属", ["有色", "稀有金属", "稀土", "铜", "铝", "钢铁", "黄金股"]),
    ("煤炭", ["煤炭"]),
    ("石化", ["石化", "石油", "油气"]),
    ("化工", ["化工"]),
    ("机械", ["工程机械", "机械"]),
    ("家电", ["家电"]),
    ("消费", ["消费", "食品", "饮料", "白酒", "酒", "农业", "旅游", "零售"]),
    ("环保", ["环保"]),
    ("基建", ["基建", "建筑", "建材", "交通", "物流"]),
    ("金融其他", ["金融"]),
]


def official_category(row) -> str:
    def on(col):
        return int(row.get(col) or 0) == 1
    if on("IS_HBETF"):
        return "货币"
    if on("IS_ZQETF"):
        return "债券"
    if on("IS_SPETF"):
        return "商品"
    if on("IS_WPETF"):
        return "跨境"
    if on("IS_HYETF"):
        return "行业"
    if on("IS_FGETF"):
        return "风格"
    if on("IS_KJETF"):
        return "宽基"
    return "其他"


def sector_tag(category: str, name: str, index_name: str) -> str:
    text = f"{name or ''} {index_name or ''}"
    if category == "货币":
        return "货币"
    if category == "债券":
        return "债券"
    if category == "商品":
        if "黄金" in text and "黄金股" not in text:
            return "商品-黄金"
        return "商品-其他"
    if category == "跨境":
        return "跨境"
    if category == "宽基":
        return "宽基"
    if category == "风格":
        if any(k in text for k in ("红利", "高股息", "低波")):
            return "风格-红利/低波"
        if "价值" in text:
            return "风格-价值"
        if "成长" in text:
            return "风格-成长"
        return "风格-其他"
    # 行业 / 其他
    for lab, kws in SECTOR_RULES:
        if any(k in text for k in kws):
            return lab
    return "行业其他" if category == "行业" else category


FOCUS_RULES = {
    "科创芯片": {"match": ["科创芯片"], "in_name_or_index": True, "core": True},
    "CPO": {"match": ["CPO", "cpo", "光模块", "光通信", "光电", "光芯片"],
            "in_name_or_index": True, "core": True},
    "半导体设备": {"match": ["半导体设备"], "in_name_or_index": False, "core": True},  # 简称
    "机器人": {"match": ["机器人"], "in_name_or_index": True, "core": True},
    "银行": {"match": ["银行"], "in_name_or_index": False, "core": True},
    "猪肉": {"match": ["猪肉", "生猪", "畜牧", "养殖", "养猪"],
            "in_name_or_index": True, "core": True},
    "传媒": {"match": ["传媒", "影视", "文娱传媒", "游戏传媒"],
            "in_name_or_index": False, "core": True},
    "传媒-相关游戏": {"match": ["游戏", "动漫"], "in_name_or_index": False, "core": False},
}


def focus_hits(name: str, index_name: str) -> list[str]:
    hits = []
    for theme, rule in FOCUS_RULES.items():
        keys = rule["match"]
        if rule["in_name_or_index"]:
            blob = f"{name} {index_name}".lower()
            ok = any(k.lower() in blob for k in keys)
        else:
            ok = any(k.lower() in (name or "").lower() for k in keys)
        if ok:
            # 传媒-相关游戏：排除已是传媒核心的
            if theme == "传媒-相关游戏" and any(k in (name or "") for k in ("传媒", "影视", "文娱传媒", "游戏传媒")):
                continue
            hits.append(theme)
    return hits


def tx_code(secucode: str, security_code: str) -> str:
    s = str(secucode).upper()
    code = str(security_code).zfill(6)
    if s.endswith(".SH"):
        return "sh" + code
    if s.endswith(".SZ"):
        return "sz" + code
    # fallback by prefix
    if code.startswith(("5", "6")):
        return "sh" + code
    return "sz" + code


# --------------- universe ---------------
def fetch_em_list() -> pd.DataFrame:
    base = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    rows, page = [], 1
    while True:
        url = f"{base}?reportName=RPT_FUND_ETFLIST&columns=ALL&pageNumber={page}&pageSize=100"
        last = None
        for i in range(4):
            try:
                r = requests.get(url, headers=UA, timeout=30)
                r.raise_for_status()
                j = r.json()
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.2 * (i + 1))
        else:
            raise RuntimeError(f"东财 ETF 列表第 {page} 页失败: {last!r}")
        res = j.get("result") or {}
        data = res.get("data") or []
        if not data:
            break
        rows.extend(data)
        log(f"东财列表 {page}/{res.get('pages')} 累计 {len(rows)}")
        if page >= int(res.get("pages") or 1):
            break
        page += 1
        time.sleep(0.12)
    return pd.DataFrame(rows)


def cmd_universe() -> pd.DataFrame:
    log("拉取东财 RPT_FUND_ETFLIST")
    raw = fetch_em_list()
    raw.to_csv(RUN / "em_etf_list_raw.csv", index=False, encoding="utf-8-sig")
    df = raw.copy()
    df["code6"] = df["SECURITY_CODE"].astype(str).str.zfill(6)
    df["tx_code"] = [tx_code(s, c) for s, c in zip(df["SECUCODE"], df["code6"])]
    df["name"] = df["SECURITY_NAME_ABBR"].astype(str)
    df["index_name"] = df["INDEX_NAME"].fillna("").astype(str)
    df["category"] = df.apply(official_category, axis=1)
    df["sector"] = [sector_tag(c, n, i) for c, n, i in zip(df["category"], df["name"], df["index_name"])]
    df["focus"] = [",".join(focus_hits(n, i)) for n, i in zip(df["name"], df["index_name"])]
    df["nav"] = pd.to_numeric(df.get("DEC_NAV"), errors="coerce")
    df["shares"] = pd.to_numeric(df.get("DEC_TOTALSHARE"), errors="coerce")
    df["aum_est"] = df["nav"] * df["shares"]  # NAV × 份额
    keep = [
        "tx_code", "code6", "SECUCODE", "name", "index_name", "INDEX_CODE",
        "category", "sector", "focus",
        "IS_HSETF", "IS_HBETF", "IS_WPETF", "IS_ZQETF", "IS_SPETF",
        "IS_KJETF", "IS_HYETF", "IS_FGETF", "IS_HSMARKET",
        "nav", "shares", "aum_est",
    ]
    out = df[keep].sort_values(["category", "sector", "tx_code"]).reset_index(drop=True)
    out.to_csv(RUN / "universe_all.csv", index=False, encoding="utf-8-sig")
    # focus map
    fmap = []
    for _, r in out.iterrows():
        if not r["focus"]:
            continue
        for th in str(r["focus"]).split(","):
            fmap.append(dict(theme=th, tx_code=r["tx_code"], name=r["name"],
                             index_name=r["index_name"], category=r["category"],
                             sector=r["sector"], aum_est=r["aum_est"]))
    pd.DataFrame(fmap).to_csv(RUN / "focus_map.csv", index=False, encoding="utf-8-sig")
    log(f"全集 {len(out)} 只")
    log("大类:\n" + out["category"].value_counts().to_string())
    log("焦点主题只数:\n" + (pd.DataFrame(fmap)["theme"].value_counts().to_string() if fmap else "无"))
    # sina contrast
    try:
        u = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeDataSimple?page=1&num=2000&sort=symbol&asc=1&node=etf_hq_fund")
        sina = pd.DataFrame(requests.get(u, headers=UA, timeout=30).json())
        sina.to_csv(RUN / "sina_etf_live.csv", index=False, encoding="utf-8-sig")
        log(f"新浪对照 {len(sina)} 只；东财有而新浪无 "
            f"{(~out['tx_code'].isin(set(sina['symbol'].str.lower()))).sum()} 只")
    except Exception as e:  # noqa: BLE001
        log(f"新浪对照失败 {e!r}")
    return out


# --------------- pull ---------------
_rate_lock = Lock()
_last_req = [0.0]


def _throttle():
    with _rate_lock:
        now = time.time()
        wait = SLEEP - (now - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()


def get_json(url, tries=3):
    last = None
    for i in range(tries):
        _throttle()
        try:
            r = requests.get(url, timeout=20, headers=UA)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 + 1.2 * i)
    raise last


def fetch_etf_tx(code: str) -> pd.DataFrame:
    rows, end = [], PULL_END
    for _ in range(8):
        u = (f"https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?"
             f"param={code},day,{PULL_START},{end},800,qfq")
        payload = get_json(u)
        block = (payload.get("data") or {}).get(code) or {}
        k = block.get("qfqday") or block.get("day") or []
        if not k:
            break
        rows = k + rows
        if len(k) < 800:
            break
        first = pd.Timestamp(k[0][0])
        if first <= pd.Timestamp(PULL_START):
            break
        end = (first - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])
    recs = []
    for r in rows:
        date, o, c, h, l, vol = r[0], r[1], r[2], r[3], r[4], r[5]
        amt = None
        # newfqkline 第 9 列（下标 8）为成交额（万元）
        if len(r) >= 9 and r[8] not in ("", None, {}):
            try:
                amt = float(r[8]) * 10000.0
            except (TypeError, ValueError):
                amt = None
        recs.append((date, o, h, l, c, vol, amt))
    df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("open", "close", "high", "low", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    df = df[df["date"] >= pd.Timestamp(PULL_START)].reset_index(drop=True)
    df["amount"] = df["amount"].fillna(df["volume"] * df["close"] * AMOUNT_SCALE)
    return df


def _pull_one(code: str, skip_existing: bool) -> dict:
    path = CACHE / f"{code}.csv"
    if skip_existing and path.exists() and path.stat().st_size > 50:
        df = pd.read_csv(path, parse_dates=["date"])
        return dict(tx_code=code, status="cached", rows=len(df),
                    start=str(df["date"].iloc[0].date()) if len(df) else "",
                    end=str(df["date"].iloc[-1].date()) if len(df) else "",
                    err="")
    try:
        df = fetch_etf_tx(code)
        if df.empty:
            return dict(tx_code=code, status="empty", rows=0, start="", end="", err="empty")
        df.to_csv(path, index=False, float_format="%.6g")
        return dict(tx_code=code, status="ok", rows=len(df),
                    start=str(df["date"].iloc[0].date()),
                    end=str(df["date"].iloc[-1].date()), err="")
    except Exception as e:  # noqa: BLE001
        return dict(tx_code=code, status="fail", rows=0, start="", end="", err=repr(e)[:200])


def cmd_pull() -> pd.DataFrame:
    uni_path = RUN / "universe_all.csv"
    if not uni_path.exists():
        cmd_universe()
    uni = pd.read_csv(uni_path)
    # 货币不拉行情（预登记：不进入回测）
    targets = uni[uni["category"] != "货币"]["tx_code"].tolist()
    log(f"待拉 {len(targets)} 只（已排除货币 {int((uni.category=='货币').sum())}）")
    meta = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(_pull_one, c, True): c for c in targets}
        for fut in as_completed(futs):
            rec = fut.result()
            meta.append(rec)
            done += 1
            if done % 50 == 0 or rec["status"] == "fail":
                ok = sum(1 for m in meta if m["status"] in ("ok", "cached"))
                log(f"拉数 {done}/{len(targets)} 成功 {ok} 最近 {rec['tx_code']} {rec['status']} {rec.get('err','')}")
    m = pd.DataFrame(meta)
    m.to_csv(RUN / "pull_meta.csv", index=False, encoding="utf-8-sig")
    log("拉数完成:\n" + m["status"].value_counts().to_string())
    fails = m[m.status == "fail"]
    if len(fails):
        log("失败清单:\n" + fails[["tx_code", "err"]].to_string(index=False))
    return m


# --------------- factors / policies ---------------
def slim_factors(df: pd.DataFrame) -> dict:
    if "date" in df.columns:
        df = df.set_index("date")
    c = df["close"].astype(float)
    F = {"close": c, "ret": c.pct_change()}
    F["ma200"] = c.rolling(200, min_periods=200).mean()
    F["mom20"] = c / c.shift(20) - 1
    F["vol60"] = F["ret"].rolling(60, min_periods=42).std() * np.sqrt(TD)
    if "amount" in df.columns:
        F["amount"] = df["amount"].astype(float)
    else:
        F["amount"] = df["volume"].astype(float) * c * AMOUNT_SCALE
    return F


def g1_volcap_T(F: dict):
    """定稿 G1 / volcap / T，一字不改阈值。"""
    c = F["close"]
    bull = ((c > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)
    g1 = hyst(F["mom20"] < 0.0, F["mom20"] > 0.01, 0.0)
    g1 = pd.Series(np.where(g1.to_numpy() > 0, 1.0, np.where(bull.to_numpy(), 0.5, 0.0)),
                   index=c.index)
    volcap = (K_VOL / F["vol60"]).clip(FLOOR, 1.0).fillna(1.0)
    slope = F["ma200"].pct_change(60)
    above = c > F["ma200"]
    T = np.select([above & (slope > 0.02), above & (slope >= -0.02), above,
                   (~above) & (slope >= -0.02)], [0, 1, 2, 3], default=4)
    T = pd.Series(T, index=c.index).where(F["ma200"].notna(), 1)
    return g1, volcap, T


def policy_baseline(F):
    g1, volcap, T = g1_volcap_T(F)
    taper = T.map(TAPER_BASE).astype(float)
    return (g1 * volcap * taper).clip(0.0, 1.0), T


def policy_3state(F):
    g1, volcap, T = g1_volcap_T(F)
    taper = T.map(TAPER_3S).astype(float)
    return (g1 * volcap * taper).clip(0.0, 1.0), T


def state3_label(T: pd.Series) -> pd.Series:
    return T.map({0: "上行", 1: "上行", 2: "震荡", 3: "震荡", 4: "下行"}).fillna("上行")


# --------------- eval ---------------
def split_masks(dates: pd.Series) -> dict:
    d = pd.to_datetime(dates)
    ev = d >= pd.Timestamp(EVAL_START)
    return {
        "train": ev & (d <= pd.Timestamp(TRAIN_END)),
        "test": ev & (d >= pd.Timestamp(TEST_START)),
        "all": ev,
    }


def metrics_block(net: pd.Series, ret: pd.Series, pos_shifted: pd.Series, mask: np.ndarray) -> dict:
    net_s = net[mask]
    ret_s = ret[mask].fillna(0.0)
    p_s = pos_shifted[mask]
    s = stats(net_s)
    b = stats(ret_s)
    avg = float(p_s.mean()) if len(p_s) else np.nan
    fx = stats(avg * ret_s) if len(p_s) >= MIN_BARS_STATS else dict(cagr=np.nan, mdd=np.nan, calmar=np.nan, sharpe=np.nan)
    n = int(mask.sum())
    out = {}
    out["n"] = n
    for k, v in s.items():
        out[k] = v
    out["bh_cagr"] = b["cagr"]
    out["bh_mdd"] = b["mdd"]
    out["bh_calmar"] = b["calmar"]
    out["d_calmar"] = (s["calmar"] - b["calmar"]) if pd.notna(s["calmar"]) and pd.notna(b["calmar"]) else np.nan
    out["d_cagr"] = (s["cagr"] - b["cagr"]) if pd.notna(s["cagr"]) and pd.notna(b["cagr"]) else np.nan
    out["d_mdd"] = (s["mdd"] - b["mdd"]) if pd.notna(s["mdd"]) and pd.notna(b["mdd"]) else np.nan
    out["d_calmar_fixed"] = (s["calmar"] - fx["calmar"]) if pd.notna(s["calmar"]) and pd.notna(fx["calmar"]) else np.nan
    out["avg_pos"] = avg
    if n >= 2:
        out["turn"] = float(p_s.diff().abs().sum() / (n / TD))
    else:
        out["turn"] = np.nan
    return out


def load_bars(code: str) -> pd.DataFrame | None:
    p = CACHE / f"{code}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"]).sort_values("date").drop_duplicates("date")
    df = df[df["close"].notna() & (df["close"] > 0)].reset_index(drop=True)
    return df if len(df) else None


def classify_exclude(row, df, ev_n, ma200_valid, avg_amt) -> str:
    if row["category"] == "货币":
        return "货币ETF"
    if df is None or len(df) < MIN_BARS_STATS:
        return "拉数失败或有效日线不足60"
    if ev_n < MIN_BARS_CROSS or ma200_valid < MIN_MA200_VALID:
        return "短历史(评估窗<250日或MA200有效日<60)"
    if pd.isna(avg_amt) or avg_amt < MIN_AVG_AMOUNT:
        return "流动性不足(近60日均额<500万元)"
    return ""


def cmd_eval() -> None:
    uni = pd.read_csv(RUN / "universe_all.csv")
    meta = pd.read_csv(RUN / "pull_meta.csv") if (RUN / "pull_meta.csv").exists() else pd.DataFrame()
    pull_status = dict(zip(meta["tx_code"], meta["status"])) if len(meta) else {}

    rows = []
    excl = []
    live = []
    n_uni = len(uni)
    for i, row in uni.iterrows():
        code = row["tx_code"]
        if (i + 1) % 100 == 0:
            log(f"评估 {i+1}/{n_uni}")
        if row["category"] == "货币":
            excl.append({**row.to_dict(), "exclude": "货币ETF", "ev_n": 0, "avg_amt_60": np.nan,
                         "first": "", "last": "", "ma200_valid": 0})
            continue
        df = load_bars(code)
        if df is None:
            excl.append({**row.to_dict(), "exclude": "拉数失败或有效日线不足60",
                         "ev_n": 0, "avg_amt_60": np.nan, "first": "", "last": "",
                         "ma200_valid": 0, "pull_status": pull_status.get(code, "missing")})
            continue
        first, last = str(df["date"].iloc[0].date()), str(df["date"].iloc[-1].date())
        ev = df["date"] >= pd.Timestamp(EVAL_START)
        ev_n = int(ev.sum())
        # liquidity on last 60 eval (or all) bars
        ev_df = df[ev] if ev.any() else df
        tail = ev_df.tail(60)
        avg_amt = float(tail["amount"].mean()) if "amount" in tail.columns and len(tail) else np.nan

        F = slim_factors(df)
        ma200_valid = int(F["ma200"].reindex(df.set_index("date").index)[ev.values].notna().sum()) if ev.any() else 0
        # ev is on df rows; F indexed by date
        ev_dates = set(df.loc[ev, "date"])
        ma200_valid = int(F["ma200"].loc[F["close"].index.isin(ev_dates)].notna().sum())

        reason = classify_exclude(row, df, ev_n, ma200_valid, avg_amt)
        rec_common = dict(tx_code=code, name=row["name"], index_name=row["index_name"],
                          category=row["category"], sector=row["sector"], focus=row["focus"],
                          first=first, last=last, ev_n=ev_n, ma200_valid=ma200_valid,
                          avg_amt_60=avg_amt, listed_after_train=first > TRAIN_END,
                          short_flag=bool(reason), exclude=reason or "")
        if reason:
            # still evaluate focus / short-history for appendix if we can
            do_eval = (bool(row["focus"]) or reason.startswith("短历史") or reason.startswith("流动性")) and ev_n >= MIN_BARS_STATS
            if not do_eval:
                excl.append(rec_common)
                continue
            rec_common["in_cross_section"] = False
        else:
            rec_common["in_cross_section"] = True

        pos_b, T = policy_baseline(F)
        pos_3, _ = policy_3state(F)
        ret = F["ret"]
        # align to date index
        dates = ret.index
        masks = split_masks(pd.Series(dates, index=dates))
        # last live signal
        last_i = dates[-1]
        st = state3_label(T).loc[last_i]
        live.append(dict(tx_code=code, name=row["name"], category=row["category"], sector=row["sector"],
                         focus=row["focus"], date=str(pd.Timestamp(last_i).date()),
                         T=int(T.loc[last_i]), state3=st,
                         close=float(F["close"].loc[last_i]),
                         mom20=float(F["mom20"].loc[last_i]) if pd.notna(F["mom20"].loc[last_i]) else np.nan,
                         vol60=float(F["vol60"].loc[last_i]) if pd.notna(F["vol60"].loc[last_i]) else np.nan,
                         pos_baseline=float(pos_b.loc[last_i]),
                         pos_3state=float(pos_3.loc[last_i]),
                         in_cross_section=rec_common["in_cross_section"]))

        for rule, pos in (("baseline", pos_b), ("state3", pos_3)):
            net, p = run_pos(pos, ret)
            rec = dict(rec_common, rule=rule)
            for split, mask in masks.items():
                m = mask.to_numpy() if hasattr(mask, "to_numpy") else np.asarray(mask)
                blk = metrics_block(net, ret, p, m)
                for k, v in blk.items():
                    rec[f"{split}_{k}"] = v
            rows.append(rec)
        if reason:
            excl.append(rec_common)

    res = pd.DataFrame(rows)
    res.to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(excl).to_csv(RUN / "exclusions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(live).to_csv(RUN / "live_signal.csv", index=False, encoding="utf-8-sig")

    # snapshots / summaries
    snap = {
        "snapshot_date": (res["last"].max() if len(res) else None),
        "eval_start": EVAL_START, "train_end": TRAIN_END, "test_start": TEST_START,
        "cost_one_way": COST, "delay": 1, "amount_scale": AMOUNT_SCALE,
        "min_bars_cross": MIN_BARS_CROSS, "min_avg_amount": MIN_AVG_AMOUNT,
        "universe_n": int(len(uni)),
        "money_market_n": int((uni.category == "货币").sum()),
        "evaluated_n": int(res["tx_code"].nunique()) if len(res) else 0,
        "cross_section_n": int(res.loc[res.in_cross_section, "tx_code"].nunique()) if len(res) else 0,
        "prereg": "runs/2026-11-14_fullmkt_single_asset/PREREG.md",
    }
    (RUN / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summaries(res, uni, pd.DataFrame(excl), snap)
    log("评估完成")


def _agg(df: pd.DataFrame, split: str) -> pd.Series:
    dcol, mcol, ccol = f"{split}_d_calmar", f"{split}_d_mdd", f"{split}_d_cagr"
    sub = df[df[f"{split}_n"] >= MIN_BARS_STATS]
    if sub.empty:
        return pd.Series(dict(n=0, med_d_calmar=np.nan, frac_d_calmar_pos=np.nan,
                              med_d_mdd=np.nan, frac_mdd_improved=np.nan,
                              med_d_cagr=np.nan, med_calmar=np.nan, med_bh_calmar=np.nan,
                              med_cagr=np.nan, med_bh_cagr=np.nan, med_mdd=np.nan,
                              med_bh_mdd=np.nan, med_avg_pos=np.nan, med_turn=np.nan,
                              med_d_calmar_fixed=np.nan))
    return pd.Series(dict(
        n=len(sub),
        med_d_calmar=sub[dcol].median(),
        frac_d_calmar_pos=float((sub[dcol] > 0).mean()),
        med_d_mdd=sub[mcol].median(),
        frac_mdd_improved=float((sub[mcol] > 0).mean()),  # d_mdd>0: 规则回撤幅度更小
        med_d_cagr=sub[ccol].median(),
        med_calmar=sub[f"{split}_calmar"].median(),
        med_bh_calmar=sub[f"{split}_bh_calmar"].median(),
        med_cagr=sub[f"{split}_cagr"].median(),
        med_bh_cagr=sub[f"{split}_bh_cagr"].median(),
        med_mdd=sub[f"{split}_mdd"].median(),
        med_bh_mdd=sub[f"{split}_bh_mdd"].median(),
        med_avg_pos=sub[f"{split}_avg_pos"].median(),
        med_turn=sub[f"{split}_turn"].median(),
        med_d_calmar_fixed=sub[f"{split}_d_calmar_fixed"].median(),
    ))


def write_summaries(res: pd.DataFrame, uni: pd.DataFrame, excl: pd.DataFrame, snap: dict) -> None:
    cs = res[res["in_cross_section"] == True]  # noqa: E712
    rows = []
    for rule in ("baseline", "state3"):
        sub = cs[cs.rule == rule]
        for split in ("train", "test", "all"):
            a = _agg(sub, split)
            rows.append(dict(rule=rule, group="ALL", split=split, **a))
            for cat, g in sub.groupby("category"):
                a = _agg(g, split)
                rows.append(dict(rule=rule, group=f"大类:{cat}", split=split, **a))
            for sec, g in sub.groupby("sector"):
                a = _agg(g, split)
                rows.append(dict(rule=rule, group=f"板块:{sec}", split=split, **a))
    pd.DataFrame(rows).to_csv(RUN / "summary_by_group.csv", index=False, encoding="utf-8-sig")

    # focus ticker table
    foc = res[res["focus"].fillna("").astype(str) != ""].copy()
    foc.to_csv(RUN / "focus_results.csv", index=False, encoding="utf-8-sig")

    # markdown report
    lines = []
    lines.append("# 全市场 A 股 ETF 单支仓位规则评估")
    lines.append("")
    lines.append(f"- 快照日：{snap.get('snapshot_date')}（Asia/Shanghai 日历下的行情末日）")
    lines.append(f"- 数据源：东财 `RPT_FUND_ETFLIST` 名单 + 腾讯 fqkline 前复权日线；成本单边 5bp，delay=1")
    lines.append(f"- 窗口：指标从 {EVAL_START} 起；train 至 {TRAIN_END}（背景）；test 从 {TEST_START} 起（强调，含已被本仓库用过的 2025+，不是新鲜留出）")
    lines.append(f"- 全集 {snap['universe_n']} 只；货币 {snap['money_market_n']} 只不回测；截面主评估 {snap['cross_section_n']} 只；另有焦点/短样本附录评估")
    lines.append("- 预登记：本目录 `PREREG.md`。本报告不是投资建议。")
    lines.append("")
    lines.append("## 过滤")
    if len(excl):
        vc = excl["exclude"].value_counts()
        lines.append("剔除原因（只数，含仅不进截面但仍可能附录评估的标的）：")
        for k, v in vc.items():
            lines.append(f"- {k}: {int(v)}")
    lines.append("")

    def fmt_block(rule, split, title):
        lines.append(f"### {title}")
        sub = [r for r in rows if r["rule"] == rule and r["split"] == split and r["group"] == "ALL"]
        if not sub:
            lines.append("无数据")
            return
        a = sub[0]
        lines.append(
            f"截面 n={int(a['n'])}；相对满仓中位 d_calmar={a['med_d_calmar']:+.3f}（为正 {a['frac_d_calmar_pos']:.0%}），"
            f"中位 d_mdd={a['med_d_mdd']:+.3f}（回撤改善 {a['frac_mdd_improved']:.0%}），"
            f"中位 d_cagr={a['med_d_cagr']:+.2%}；"
            f"规则中位卡玛 {a['med_calmar']:.3f} vs 满仓 {a['med_bh_calmar']:.3f}；"
            f"规则中位年化 {a['med_cagr']:.2%} vs 满仓 {a['med_bh_cagr']:.2%}；"
            f"规则中位最大回撤 {a['med_mdd']:.2%} vs 满仓 {a['med_bh_mdd']:.2%}；"
            f"中位平均仓位 {a['med_avg_pos']:.2f}，中位年换手 {a['med_turn']:.1f}；"
            f"中位 d_calmar_fixed={a['med_d_calmar_fixed']:+.3f}。"
        )
        # categories
        lines.append("")
        lines.append("| 分组 | n | 中位d_calmar | 为正 | 中位d_mdd | 回撤改善 | 中位d_cagr | 规则卡玛 | 满仓卡玛 |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        cats = [r for r in rows if r["rule"] == rule and r["split"] == split and (r["group"].startswith("大类:") or r["group"].startswith("板块:"))]
        # show 大类 first then 板块 with n>=5
        for r in sorted(cats, key=lambda x: (0 if x["group"].startswith("大类") else 1, -x["n"], x["group"])):
            if r["group"].startswith("板块:") and r["n"] < 3:
                continue
            lines.append(
                f"| {r['group']} | {int(r['n'])} | {r['med_d_calmar']:+.3f} | {r['frac_d_calmar_pos']:.0%} | "
                f"{r['med_d_mdd']:+.3f} | {r['frac_mdd_improved']:.0%} | {r['med_d_cagr']:+.2%} | "
                f"{r['med_calmar']:.3f} | {r['med_bh_calmar']:.3f} |"
            )
        lines.append("")

    lines.append("## 基线 G1 × volcap × taper")
    fmt_block("baseline", "all", "全样本（2019 起或上市后）")
    fmt_block("baseline", "test", "测试段 2023 起（强调）")
    fmt_block("baseline", "train", "训练段至 2022 末（背景）")

    lines.append("## 预登记 3 态规则")
    lines.append("上行(T0/T1)×1.0，震荡(T2/T3)×0.5，下行(T4)×0.5；G1 与 volcap 同定稿。")
    fmt_block("state3", "all", "全样本")
    fmt_block("state3", "test", "测试段 2023 起")
    fmt_block("state3", "train", "训练段至 2022 末")

    lines.append("## 焦点主题")
    if len(foc):
        for theme in ["科创芯片", "CPO", "半导体设备", "机器人", "银行", "猪肉", "传媒", "传媒-相关游戏"]:
            # rows where focus contains theme
            hit = foc[foc["focus"].fillna("").apply(lambda s: theme in str(s).split(","))]
            lines.append(f"### {theme}")
            if hit.empty:
                lines.append("官方名单中无匹配基金名称/指数名称的 ETF。" if theme == "CPO" else "无匹配。")
                lines.append("")
                continue
            # unique tickers
            one = hit[hit.rule == "baseline"].sort_values(["in_cross_section", "avg_amt_60"], ascending=[False, False])
            lines.append(f"匹配 {one['tx_code'].nunique()} 只；进入截面 {int(one['in_cross_section'].sum())} 只。")
            lines.append("| 代码 | 名称 | 指数 | 首日 | 末日 | 截面 | 评估日 | test d_calmar(基线) | test d_mdd | test d_cagr | test 年化 规则/满仓 | test MDD 规则/满仓 | 3态 test d_calmar |")
            lines.append("|---|---|---|---|---|---|---:|---:|---:|---:|---|---|---:|")
            s3 = hit[hit.rule == "state3"].set_index("tx_code")
            for r in one.itertuples():
                s = s3.loc[r.tx_code] if r.tx_code in s3.index else None
                def f(x, pct=False, sgn=False):
                    if x is None or (isinstance(x, float) and pd.isna(x)):
                        return "NA"
                    if pct and sgn:
                        return f"{x:+.2%}"
                    if pct:
                        return f"{x:.2%}"
                    if sgn:
                        return f"{x:+.3f}"
                    return f"{x:.3f}"
                tc = f(s["test_d_calmar"], sgn=True) if s is not None else "NA"
                lines.append(
                    f"| {r.tx_code} | {r.name} | {r.index_name} | {r.first} | {r.last} | "
                    f"{'是' if r.in_cross_section else '否'} | {r.ev_n} | "
                    f"{f(r.test_d_calmar, sgn=True)} | {f(r.test_d_mdd, sgn=True)} | {f(r.test_d_cagr, pct=True, sgn=True)} | "
                    f"{f(r.test_cagr, pct=True)} / {f(r.test_bh_cagr, pct=True)} | "
                    f"{f(r.test_mdd, pct=True)} / {f(r.test_bh_mdd, pct=True)} | {tc} |"
                )
            lines.append("")
    else:
        lines.append("无焦点结果。")

    lines.append("## 文件")
    for fn in ["PREREG.md", "universe_all.csv", "focus_map.csv", "pull_meta.csv",
               "results_long.csv", "summary_by_group.csv", "focus_results.csv",
               "exclusions.csv", "live_signal.csv", "snapshot.json", "REPORT.md"]:
        lines.append(f"- `runs/2026-11-14_fullmkt_single_asset/{fn}`")
    (RUN / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"写报告 {(RUN/'REPORT.md')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["universe", "pull", "eval", "all"], nargs="?", default="all")
    args = ap.parse_args()
    if args.cmd in ("universe", "all"):
        cmd_universe()
    if args.cmd in ("pull", "all"):
        cmd_pull()
    if args.cmd in ("eval", "all"):
        cmd_eval()


if __name__ == "__main__":
    main()
