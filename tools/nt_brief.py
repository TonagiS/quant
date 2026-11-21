"""每日汇金播报：主力资金流向 + 汇金 3/14 日流向 + 影子规则判定 + 历史类比。

用法
----
venv\\Scripts\\python.exe tools\\nt_brief.py

数据口径
--------
* 汇金代理：沪市 9 只宽基 ETF 份额合计（亿份），来自 etf_shares_hist.csv。
* 主力资金：东财 push2 分钟资金流，沪（1.000001）+ 深（0.399001）最新一根加总。
* 指数：腾讯实时行情。
* 影子规则：冻结版 14 日累计 ≤ -10%，5 个交易日后防守 20 日（见 nt_signal_shadow.py）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
OUT = ROOT / "data" / "national_team"
HIST = OUT / "etf_shares_hist.csv"

UA = {"User-Agent": "Mozilla/5.0"}


def http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={**UA, "Referer": "https://data.eastmoney.com/"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace"))


def fetch_flow() -> dict:
    """返回最新一根分钟资金流（亿元），沪+深合计。"""
    out = {"time": "", "main": 0.0, "xl": 0.0, "l": 0.0, "m": 0.0, "s": 0.0}
    for secid in ("1.000001", "0.399001"):
        url = (
            "http://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=0&klt=1"
            "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
            "&ut=b2884a393a59ad64002292a3e90d46a5&secid=" + secid
        )
        d = http_json(url)
        kl = (d.get("data") or {}).get("klines") or []
        if not kl:
            continue
        t, main, s, m, l, xl = kl[-1].split(",")[:6]
        out["time"] = t
        out["main"] += float(main) / 1e8
        out["s"] += float(s) / 1e8
        out["m"] += float(m) / 1e8
        out["l"] += float(l) / 1e8
        out["xl"] += float(xl) / 1e8
    return out


def fetch_quotes(codes: list[str]) -> dict[str, dict]:
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)
    req = urllib.request.Request(url, headers=UA)
    body = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "replace")
    out = {}
    for line in body.splitlines():
        if "~" not in line or "=" not in line:
            continue
        code = line.split("=")[0].split("_")[-1]
        f = line.split('"')[1].split("~")
        if len(f) < 6:
            continue
        try:
            cur, prev = float(f[3]), float(f[4])
        except ValueError:
            continue
        out[code] = {"name": f[1], "cur": cur, "prev": prev, "pct": (cur / prev - 1) * 100 if prev else 0.0}
    return out


def load_shares() -> pd.DataFrame:
    h = pd.read_csv(HIST, parse_dates=["date"])
    p = h.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4  # 亿份
    return p


def cum14_series() -> pd.Series:
    """与 nt_signal_shadow 完全一致的 cum14（逐日共同代码口径，避免基金新发/退市造成虚假跳变）。"""
    import nt_signal_shadow as shadow

    dates, cum = shadow.load_shares()
    return pd.Series(cum, index=pd.to_datetime(dates)).dropna()


def rule_state(cum14: pd.Series) -> dict:
    d = pd.DataFrame({"cum14": cum14})
    sig = d.cum14 <= -10
    last_sig = d.index[sig].max() if sig.any() else None
    last_def_end, last_def_start = None, None
    idx = list(d.index)
    for i, v in enumerate(d.cum14.values):
        if v <= -10:
            lo = min(i + 5, len(idx) - 1)
            hi = min(i + 25, len(idx))
            last_def_start, last_def_end = idx[lo], idx[hi - 1]
    return {"cum14": d.cum14.iloc[-1], "signal": bool(sig.iloc[-1]),
            "last_sig": last_sig, "def_start": last_def_start, "def_end": last_def_end}


def fwd_stat(cum14: pd.Series, thr: float, days: int = 20) -> dict:
    px = pd.read_csv(ROOT / "data" / "cache2019" / "sh510300.csv", encoding="utf-8-sig")
    px["date"] = pd.to_datetime(px["date"])
    px = px.set_index("date").sort_index()["close"]
    pos = {dt: i for i, dt in enumerate(px.index)}
    rets = []
    for dt, v in cum14.items():
        i = pos.get(dt)
        if v >= thr and i is not None and i + days < len(px):
            rets.append(px.iloc[i + days] / px.iloc[i] - 1)
    s = pd.Series(rets)
    return {"n": len(s), "mean": s.mean() * 100, "median": s.median() * 100,
            "win": (s > 0).mean() * 100}


def group_cfg() -> dict:
    """读 dsh-agent/group.json（utf-8-sig：BOM 也算数，别因为一个 BOM 静默返回空）。"""
    cfg_path = Path(r"E:\Code\dsh-agent\group.json")
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8-sig")) if cfg_path.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def at_list() -> list[str]:
    """群播报末尾要 @ 的人（配置在 dsh-agent/group.json 的 at 字段）。"""
    ats = group_cfg().get("at") or []
    if isinstance(ats, (str, int)):
        ats = [ats]
    return [str(x).strip() for x in ats if str(x).strip()]


def dm_targets() -> list[str]:
    """额外收播报的 QQ（配置在 dsh-agent/group.json 的 dm 字段）。"""
    dms = group_cfg().get("dm") or []
    if isinstance(dms, (str, int)):
        dms = [dms]
    return [str(x).strip() for x in dms if str(x).strip()]


def notify(text: str, group: str = "", qq: str = "") -> None:
    """推送到 AstrBot 的 dsh_bridge（默认 QQ 私聊，给 group 则发到该群并 @ 名单，给 qq 则私聊该号）。"""
    bridge = Path(r"E:\Code\dsh-agent\bridge.json")
    cfg = json.loads(bridge.read_text(encoding="utf-8")) if bridge.exists() else {}
    port = int(cfg.get("port", 8721))
    token = str(cfg.get("token", ""))
    payload = {"text": text, "images": []}
    if group:
        payload["group"] = str(group)
        ats = at_list()
        if ats:
            payload["at"] = ats
    elif str(qq).strip():
        payload["qq"] = str(qq).strip()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/notify",
        data=body,
        headers={"X-Token": token, "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        rsp = r.read().decode("utf-8", "replace")
    low = rsp.lower().replace(" ", "")
    if '"ok":false' in low or low.startswith("error"):
        raise RuntimeError("桥返回失败：%s" % rsp[:120])
    print("[notified] -> %s" % ("group " + group if group else ("private " + str(qq) if qq else "private")))


def notify_all(text: str, group: str = "") -> None:
    """先按原样发（群或主人私聊），再给 group.json 里 dm 名单各私聊一份。"""
    notify(text, group)
    for qq in dm_targets():
        try:
            notify(text, qq=qq)
        except Exception as exc:  # noqa: BLE001
            print("[dm %s 失败] %s" % (qq, exc))


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true", help="推送到 QQ")
    ap.add_argument("--short", action="store_true", help="精简版（主力/3日/14日 三行）")
    ap.add_argument("--group", default="", help="推送到指定 QQ 群（默认私聊）")
    args = ap.parse_args(argv)

    sh = load_shares()
    tot = sh.sum(axis=1)
    latest = tot.index[-1]
    dates = list(tot.index)
    i = len(dates) - 1

    def chg(n: int) -> float:
        return tot.iloc[i] - tot.iloc[i - n]

    chg3, chg14 = chg(3), chg(14)

    # 金额估算：按各 ETF 最新价（沪市代码加 sh 前缀）
    q = fetch_quotes(["sh" + str(c) for c in sh.columns])
    money3 = money14 = 0.0
    for code in sh.columns:
        px = (q.get("sh" + str(code)) or {}).get("cur")
        if not px:
            continue
        d3 = sh[code].iloc[i] - sh[code].iloc[i - 3]
        d14 = sh[code].iloc[i] - sh[code].iloc[i - 14]
        money3 += d3 * px
        money14 += d14 * px

    cum14 = cum14_series()
    st = rule_state(cum14)
    flow = fetch_flow()
    idx = fetch_quotes(["sh000001", "sz399001", "sz399006", "sh000300", "sh000905", "sh000852"])
    stat20 = fwd_stat(cum14, 20.0, 20)

    today = time.strftime("%Y-%m-%d")
    if args.notify and flow["time"][:10] != today:
        print("[skip] 今天没有资金流数据（非交易日或接口异常），不推送")
        return

    ts = datetime.now().strftime("%m-%d %H:%M")
    dstr = latest.strftime("%m-%d")

    if args.short:
        c3 = chg3 / tot.iloc[i - 3] * 100
        rc = float(st["cum14"])  # 规则口径（逐日共同代码百分比相加），显示与判定统一用它
        verdict = "⚠️触发减仓规则" if st["signal"] else "未触发减仓规则"
        text = "\n".join([
            f"{ts}｜主力 {flow['main']:+.0f} 亿",
            "",
            "国家队（汇金）",
            "3日(至 %s) %+.2f%%" % (dstr, c3),
            "14日(至 %s) %+.2f%% %s -10%%，%s" % (
                dstr, rc, "≤" if rc <= -10 else "＞", verdict),
        ])
        print(text)
        outdir = ROOT / "runs" / (date.today().isoformat() + "_nt_brief")
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "brief_short.md").write_text(text + "\n", encoding="utf-8")
        if args.notify:
            notify_all(text, args.group)
        return

    lines = [
        f"【汇金播报 {ts}】份额截至 {dstr}",
        "",
        f"主力资金（两市，截至 {flow['time'][-5:] or '--:--'}）：%+.0f 亿"
        "（超大单 %+.0f / 大单 %+.0f / 小单 %+.0f）"
        % (flow["main"], flow["xl"], flow["l"], flow["s"]),
        "指数：" + "  ".join(
            f"{idx[c]['name']} {idx[c]['cur']:.2f} ({idx[c]['pct']:+.2f}%)"
            for c in ["sh000001", "sz399001", "sz399006", "sh000300"]
            if c in idx
        ),
        "",
        "汇金 3 日：%+.1f 亿份（%+.2f%%，≈ %+.0f 亿元）" % (chg3, chg3 / tot.iloc[i - 3] * 100, money3),
        "汇金 14 日：%+.1f 亿份（%+.2f%%，≈ %+.0f 亿元）" % (chg14, chg14 / tot.iloc[i - 14] * 100, money14),
        "",
        "影子规则：cum14=%+.2f%% → %s（阈值 -10%%）" % (
            st["cum14"], "⚠️ 触发" if st["signal"] else "未触发，正常"),
        "最近信号 %s，防守窗口 %s ~ %s" % (
            st["last_sig"].strftime("%m-%d") if st["last_sig"] is not None else "-",
            st["def_start"].strftime("%m-%d") if st["def_start"] is not None else "-",
            st["def_end"].strftime("%m-%d") if st["def_end"] is not None else "-"),
        "",
        "历史类比：14 日累计买入 ≥20%% 时，后 20 日 510300 中位 %+.2f%%、均值 %+.2f%%、胜率 %.0f%%（n=%d）"
        % (stat20["median"], stat20["mean"], stat20["win"], stat20["n"]),
    ]
    text = "\n".join(lines)
    print(text)

    outdir = ROOT / "runs" / (date.today().isoformat() + "_nt_brief")
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "brief.md").write_text(text + "\n", encoding="utf-8")
    print("\n[saved]", outdir / "brief.md")

    if args.notify:
        notify_all(text, args.group)


if __name__ == "__main__":
    main()
