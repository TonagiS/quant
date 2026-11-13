"""
标的池定义与加载：ETF（原池+扩展）、长历史指数、经典蓝筹股。
与 backtest_composite.py、check_pool.py 放在同一文件夹（indicators/code）。

用法（backtest_composite.py --pool ...）：
  base         原 ETF_POOL（自动修正 159xxx 的 sz 前缀）
  ext          base + EXTRA_ETF（默认）
  long         宽基+中证一级行业指数（基日 2004-12-31，历史最长，指数不可直接交易）
  stocks       经典蓝筹股（前复权）
  可用 + 组合，如 long+stocks（建议 --start 20070101）

注意：
  * 中证行业指数 2009 年前的点位是回算值，不是当时真实可交易的；仅用于检验规则方向。
  * 经典股票是事后挑选的大盘蓝筹，存在幸存者偏差（已混入不少长期落后者），只能检验方向，不能看绝对收益。
  * 数据缓存统一在仓库 `data/` 下：cache2019 / cache2007 / cache_base；不同起点不要混用。
"""
import glob
import os
import time
from collections import OrderedDict
from datetime import datetime
from functools import lru_cache

import numpy as np
import pandas as pd

from backtest_structure import load_hist

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data")
CACHE_2019 = os.path.join(DATA_DIR, "cache2019")
CACHE_2007 = os.path.join(DATA_DIR, "cache2007")
CACHE_BASE = os.path.join(DATA_DIR, "cache_base")

# 缓存目录可用环境变量覆盖：PowerShell 里 $env:QUANT_CACHE="data\cache_new"
CACHE_DIR = os.environ.get("QUANT_CACHE", CACHE_2019)


@lru_cache(maxsize=16)
def cache_start(cache_dir: str):
    """缓存目录内所有 CSV 的最早日期；用于拒绝"起点早于缓存"的静默短样本。"""
    paths = glob.glob(os.path.join(cache_dir, "*.csv"))
    starts = []
    for p in paths:
        try:
            d = pd.read_csv(p, usecols=["date"], nrows=1)
            if len(d):
                starts.append(pd.to_datetime(d["date"].iloc[0]))
        except Exception:
            continue
    return min(starts) if starts else None


def fix_prefix(code):
    """ETF/股票代码前缀规范化（指数不要用这个函数）。
    159xxx/16xxxx 在深交所 -> sz；5xxxxx、6xxxxx 在上交所 -> sh；0xxxxx、3xxxxx 在深交所 -> sz。"""
    num = code[-6:]
    if num.startswith(("15", "16")): return "sz" + num
    if num.startswith(("5", "6")): return "sh" + num
    if num.startswith(("0", "3")): return "sz" + num
    return code


# ---- 扩展 ETF（上市日期来自公开资料/常识，未能逐一核验；拉取失败的会在失败名单里列出）----
EXTRA_ETF = OrderedDict([
    # 宽基/风格/红利（多数 2005-2016 年上市，历史较长）
    ("sh510050", "上证50"),          # 2005-02 上市（已核实）
    ("sh510180", "上证180"),         # 2006-05 上市（已核实）
    ("sh510880", "红利"),
    ("sz159901", "深证100"),
    ("sz159902", "中小板"),
    ("sz159905", "深证红利"),
    ("sh510230", "上证180金融"),
    ("sh510500", "中证500"),
    ("sh512100", "中证1000"),
    ("sz159949", "创业板50"),
    # 行业/主题
    ("sh512760", "芯片"),
    ("sh512980", "传媒"),
    ("sh515880", "通信"),
    ("sh512580", "环保"),
    ("sh512070", "非银金融"),
    ("sh512290", "生物医药"),
    ("sh516160", "新能源"),
    ("sh516510", "云计算"),
    # 跨境（注意 QDII 溢价/限购会让价格偏离净值）
    ("sh513100", "纳指100"),
    ("sh513500", "标普500"),
    ("sh513050", "中概互联"),
    ("sz159920", "恒生"),
])

# ---- 长历史指数（新浪/腾讯日线；成交额用成交量代替，仅用于比值/百分位）----
LONG_INDEX = OrderedDict([
    # 宽基
    ("sh000016", "上证50指数"),
    ("sh000300", "沪深300指数"),
    ("sh000905", "中证500指数"),
    ("sz399006", "创业板指"),
    ("sh000015", "上证红利指数"),
    # 中证800 一级行业指数（基日 2004-12-31）；000932 已核实，其余按编号约定，请用 check_pool.py 确认
    ("sh000928", "中证能源"),
    ("sh000929", "中证材料"),
    ("sh000930", "中证工业"),
    ("sh000931", "中证可选消费"),
    ("sh000932", "中证主要消费"),
    ("sh000933", "中证医药卫生"),
    ("sh000934", "中证金融"),
    ("sh000935", "中证信息技术"),
    ("sh000936", "中证通信服务"),
    ("sh000937", "中证公用事业"),
])

# ---- 经典蓝筹（沪深主板，涨跌停±10%；覆盖各行业，含长期落后者以减轻幸存者偏差）----
CLASSIC_STOCKS = OrderedDict([
    ("sh600519", "贵州茅台"), ("sz000858", "五粮液"), ("sh600887", "伊利股份"),
    ("sh600036", "招商银行"), ("sh601398", "工商银行"), ("sh601318", "中国平安"),
    ("sh600030", "中信证券"), ("sz000002", "万科A"), ("sh600276", "恒瑞医药"),
    ("sz000651", "格力电器"), ("sz000333", "美的集团"), ("sh600900", "长江电力"),
    ("sh601857", "中国石油"), ("sh600028", "中国石化"), ("sh600031", "三一重工"),
    ("sh600585", "海螺水泥"), ("sh601088", "中国神华"), ("sh600104", "上汽集团"),
    ("sz002415", "海康威视"), ("sz000725", "京东方A"), ("sh600309", "万华化学"),
    ("sh600050", "中国联通"),
])


def get_pool(name, etf_pool):
    """返回 OrderedDict[code] = (显示名, kind)，kind in {etf, stock, index}"""
    out = OrderedDict()

    def add(d, kind):
        for c, n in d.items():
            cc = c if kind == "index" else fix_prefix(c)
            if cc not in out:
                out[cc] = (n, kind)

    for part in name.split("+"):
        if part == "base":
            add(etf_pool, "etf")
        elif part == "ext":
            add(etf_pool, "etf"); add(EXTRA_ETF, "etf")
        elif part == "long":
            add(LONG_INDEX, "index")
        elif part == "stocks":
            add(CLASSIC_STOCKS, "stock")
        else:
            raise ValueError(f"未知标的池: {part}（可选 base/ext/long/stocks，用 + 组合）")
    return out


def _save_cache(df, path):
    """缓存写入失败（目录无权限等）不应让整次拉取失败/重试"""
    try:
        df.to_csv(path, index=False)
        cache_start.cache_clear()
    except Exception as e:
        print(f"  [缓存写入失败，已忽略] {path}: {e!r}")


def validate_px(df, code):
    """价格合法性：不允许非正价格、单日涨跌幅>50%（典型的复权错误），否则进失败名单，不污染面板"""
    c = pd.to_numeric(df["close"], errors="coerce")
    if (c <= 0).any():
        raise ValueError(f"{code} 存在非正价格(min={c.min():.2f})，疑似复权错误")
    r = c.pct_change().abs()
    if (r > 0.5).any():
        raise ValueError(f"{code} 存在单日涨跌幅>50%(max={r.max():.2f})，疑似复权/数据错误")


def load_stock_hfq(code, start, cache_dir):
    """个股用后复权(hfq)：腾讯前复权对长期高分红/拆股的股票会算出负价格。收益率只看相对变化，hfq 不影响。"""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"stk_{code}_hfq_{start}.csv")
    today = datetime.now().strftime("%Y%m%d")
    if os.path.exists(path):
        mday = time.strftime("%Y%m%d", time.localtime(os.path.getmtime(path)))
        if mday == today:
            try:
                c = pd.read_csv(path, parse_dates=["date"])
                if len(c) > 20: return c
            except Exception:
                pass
    import akshare as ak
    last_err = None
    for _ in range(3):
        try:
            df = ak.stock_zh_a_hist_tx(symbol=code, start_date=start, end_date=today, adjust="hfq")
            if df is None or len(df) < 20:
                raise ValueError("腾讯接口返回空或数据极少")
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            if "amount" in df.columns:
                df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            elif "volume" in df.columns:
                df["amount"] = pd.to_numeric(df["volume"], errors="coerce") * df["close"] * 100
            else:
                df["amount"] = np.nan
            df = df[["date", "close", "amount"]].dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
            validate_px(df, code)
            _save_cache(df, path)
            return df
        except ValueError as e:      # 数据本身有问题，重试没有意义
            if "返回空" not in str(e):
                raise
            last_err = e
            time.sleep(1.0)
        except Exception as e:
            last_err = e
            time.sleep(1.0)
    raise RuntimeError(f"{code} 拉取失败: {last_err!r}")


def load_index(code, start, cache_dir=None):
    cache_dir = cache_dir or CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"idx_{code}_{start}.csv")
    today = datetime.now().strftime("%Y%m%d")
    if os.path.exists(path):
        mday = time.strftime("%Y%m%d", time.localtime(os.path.getmtime(path)))
        if mday == today:
            try:
                c = pd.read_csv(path, parse_dates=["date"])
                if len(c) > 20: return c
            except Exception:
                pass

    import akshare as ak
    last_err = None
    for fn_name in ("stock_zh_index_daily", "stock_zh_index_daily_tx"):
        fn = getattr(ak, fn_name, None)
        if fn is None: continue
        for _ in range(2):
            try:
                df = fn(symbol=code)
                if df is None or len(df) < 20:
                    raise ValueError("返回空或过短")
                df = df.copy()
                df["date"] = pd.to_datetime(df["date"])
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                if "amount" in df.columns:
                    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
                elif "volume" in df.columns:
                    df["amount"] = pd.to_numeric(df["volume"], errors="coerce")
                else:
                    df["amount"] = np.nan
                df = df[["date", "close", "amount"]].dropna(subset=["close"]).sort_values("date")
                last_day = df["date"].iloc[-1]
                if last_day < pd.Timestamp.now() - pd.Timedelta(days=20):
                    raise ValueError(f"数据已过期(最后日期 {last_day.date()})，换源重试")
                df = df[df["date"] >= pd.to_datetime(start)].reset_index(drop=True)
                if len(df) < 20:
                    raise ValueError("起始日之后数据过短")
                _save_cache(df, path)
                return df
            except Exception as e:
                last_err = e
                time.sleep(1.0)
    raise RuntimeError(f"{code} 指数拉取失败: {last_err!r}")


def load_one(code, kind, start, cache_dir=None):
    """返回 (df, 是否为指数替代)。df 列: date, close, amount。个股走后复权，所有标的都做价格合法性校验。"""
    cache_dir = cache_dir or CACHE_DIR
    req = pd.to_datetime(start)
    cs = cache_start(cache_dir)
    if cs is not None and req < cs - pd.Timedelta(days=30):
        raise ValueError(f"缓存目录 {cache_dir} 最早 {cs.date()}，晚于请求起点 {start}；"
                         f"拒绝静默短样本，请换 --cache 或先补缓存")
    if kind == "index":
        df = load_index(code, start, cache_dir)
        validate_px(df, code)
        return df, False
    if kind == "stock":
        df = load_stock_hfq(code, start, cache_dir)
        return df[pd.to_datetime(df["date"]) >= pd.to_datetime(start)].reset_index(drop=True), False
    df, is_idx = load_hist(code, start, cache_dir)
    # 缓存按代码命名、不区分 start：这里统一按 --start 截断，避免读到更早区间的缓存而悄悄改变样本
    df = df[pd.to_datetime(df["date"]) >= pd.to_datetime(start)].reset_index(drop=True)
    validate_px(df, code)
    return df, is_idx
