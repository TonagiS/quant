"""
14:30 尾盘决策脚本（动量排序 + 60 日线 + 逆波动加权）

已验证（仅 ETF 主题池、2019-2026、同一批数据，不是样本外）：
  * 动量排序（20/60/120 日截面排名）与 60 日线过滤具备参数稳健性（P1-ROBUST-01）；
  * 逆波动加权只在 ext 池有增益（base 无、指数池无）；
  * 成本脆弱：盈亏平衡 25bp（base）/ 29bp（ext）单边，换手高，实盘成本会显著吃掉超额（P2-COST-01/P2-TURN-01）。
已移除（均无证据）：拥挤度>85、顶部钝化/结构、量能确认。
保留但未验证：底部钝化/结构，仅作观察项。

依赖：akshare pandas numpy requests；需 indicators/code/macd_structure.py 和同目录 portfolio.json
环境变量：DEEPSEEK_API_KEY（必填，不要写进代码）、DEEPSEEK_MODEL（可选，默认 deepseek-chat）
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from macd_structure import ETF_POOL, DEFAULTS, calc_macd, analyze, label  # noqa: E402

PORTFOLIO_FILE = os.path.join(HERE, "portfolio.json")
LOG_DIR = os.path.join(HERE, "logs")


# ==========================================
# 实时行情
# ==========================================
def parse_tx_text(text):
    """解析腾讯批量行情文本，返回 {code: {...}}。解析失败的行会打印出来，不再静默跳过。"""
    rt = {}
    for line in text.split(";"):
        line = line.strip()
        if not line:
            continue
        try:
            parts = line.split("~")
            code = parts[0].split('="')[0].replace("v_", "").strip()   # v_sh510300="1 -> sh510300
            price, prev = float(parts[3]), float(parts[4])
            volume = float(parts[6])                                    # 手
            if price <= 0 or prev <= 0:
                continue                                                # 停牌/无数据
            try:
                amount = float(parts[37]) * 1e4                         # 成交额，万元 -> 元
            except (IndexError, ValueError):
                amount = volume * 100 * price
            rt[code] = {"price": price, "prev_close": prev,
                        "today_mom": (price / prev - 1) * 100,
                        "volume": volume, "amount": amount}
        except Exception as e:  # noqa: BLE001
            print(f"实时行情解析失败: {line[:40]!r} -> {e!r}")
    return rt


def get_tx_realtime(codes):
    try:
        resp = requests.get("http://qt.gtimg.cn/q=" + ",".join(codes), timeout=8)
        resp.encoding = "gbk"
        return parse_tx_text(resp.text)
    except Exception as e:  # noqa: BLE001
        print(f"实时数据请求失败: {e!r}")
        return {}


# ==========================================
# 主流程
# ==========================================
def build_table(realtime):
    import akshare as ak

    now = datetime.now()
    end_date = now.strftime("%Y%m%d")
    start_date = (now - pd.Timedelta(days=400)).strftime("%Y%m%d")
    results, skipped = [], []

    for code, name in ETF_POOL.items():
        try:
            rt = realtime.get(code)
            if not rt:
                raise ValueError("无实时行情")
            df = ak.stock_zh_a_hist_tx(symbol=code, start_date=start_date, end_date=end_date, adjust="qfq")
            if df is None or len(df) < 150:
                raise ValueError("历史数据不足150根")
            df = df.sort_values("date").reset_index(drop=True)
            if str(df["date"].iloc[-1]).replace("-", "") == end_date:
                df = df.iloc[:-1]                                   # 以实时数据为准
            close = pd.to_numeric(df["close"], errors="coerce").dropna().reset_index(drop=True)
            amount = pd.to_numeric(df["amount"], errors="coerce").dropna().reset_index(drop=True)

            price = rt["price"]
            close_t = np.append(close.values, price)                # 追加14:30临时K线（预判）
            dif, _ = calc_macd(close_t)
            tags = []
            # 只保留底部信号；顶部钝化/结构已移除（无证据）
            state, age = analyze(close_t, dif, "bottom", **DEFAULTS)
            if state not in ("无", "数据不足"):
                tags.append(label(state, age, "bottom"))
            vol20 = float(pd.Series(close_t).pct_change().tail(20).std() * 100)

            results.append({
                "代码": code, "名称": name, "最新价": round(price, 3),
                "今日涨跌(%)": round(rt["today_mom"], 2),
                "1月动量(%)": round((price / close.iloc[-20] - 1) * 100, 2),
                "3月动量(%)": round((price / close.iloc[-60] - 1) * 100, 2),
                "20日波动(%)": round(vol20, 2),
                "底部结构": " / ".join(tags) if tags else "无",
                ">60日线": "✅" if price > close.tail(60).mean() else "❌",
            })
        except Exception as e:  # noqa: BLE001
            skipped.append((name, repr(e)[:70]))

    print(f"成功 {len(results)} 只，跳过 {len(skipped)} 只")
    for name, msg in skipped:
        print(f"  跳过 {name}: {msg}")
    return results


def value_portfolio(portfolio, price_dict):
    total, lines = 0.0, ["【我的当前持仓】"]
    for code, info in portfolio["holdings"].items():
        if info["shares"] <= 0:
            continue
        price = price_dict.get(code)
        if price is None:
            print(f"⚠️ {info['name']}({code}) 没有实时价，暂按成本价估值，仓位百分比可能不准")
            price = info["cost"]
        total += info["shares"] * price
        pnl = (price / info["cost"] - 1) * 100 if info["cost"] else 0
        lines.append(f"- {info['name']} ({code}): 持有 {info['shares']} 份，成本 {info['cost']}，"
                     f"现价 {round(price, 3)}，盈亏 {pnl:.1f}%")
    assets = total + portfolio["cash"]
    return assets, (total / assets * 100 if assets else 0), "\n".join(lines)


def print_dashboard(results, assets, pos_pct):
    results.sort(key=lambda x: x["今日涨跌(%)"], reverse=True)
    print("\n" + "=" * 100)
    print(f"{'14:30 尾盘决策（动量排序 + 60日线；顶部/拥挤度/量能已移除）':^80}")
    print("=" * 100)
    print(f"{'板块':<12}{'今日':>8}{'1月':>9}{'3月':>9}{'20日波动':>9}  {'底部结构':<22}{'>60日线'}")
    print("-" * 100)
    for r in results:
        print(f"{r['名称']:<12}{r['今日涨跌(%)']:>7}%{r['1月动量(%)']:>8}%{r['3月动量(%)']:>8}%"
              f"{r['20日波动(%)']:>8}%  {r['底部结构']:<22}{r['>60日线']}")
    print("=" * 100)
    print(f"当前总资产: {assets:.0f} 元 | 当前实际仓位: {pos_pct:.1f}%\n")


def build_prompt(holdings_str, assets, cash, pos_pct, results):
    data = "板块 | 今日涨跌% | 1月动量% | 3月动量% | 20日波动% | 底部结构 | >60日线\n"
    for r in results:
        data += (f"{r['名称']} | {r['今日涨跌(%)']}% | {r['1月动量(%)']}% | {r['3月动量(%)']}% | "
                 f"{r['20日波动(%)']}% | {r['底部结构']} | {r['>60日线']}\n")
    return f"""
你是一名冷静的量化风控总监。现在是交易日 14:30。
{holdings_str}
总资产：{assets:.0f}元，现金：{cash}元，当前总仓位：{pos_pct:.1f}%。

【14:30 实时截面数据】
{data}
已验证的规则（仅 ETF 主题池、2019-2026、同一批数据、不是样本外）：
- 动量排序：20/60/120 日动量截面排名的均值，排名靠前的板块更值得持有。
- 趋势过滤：收盘价在 60 日线之上才算合格候选。
- 逆波动加权：权重与 20 日波动成反比；本脚本只给波动，不计算买卖数量。
- 成本提示：盈亏平衡约 25~29bp 单边，换手很高，实盘成本会显著吃掉超额。

已移除、不要再提的信号：拥挤度>85、顶部钝化/顶部结构、量能确认（均无证据）。
仍保留但未验证：底部钝化/结构只作观察项，出现不代表买点。

请基于数据给出定性建议，禁止自己计算买卖数量：
1.【风险提示】持仓里哪些标的跌破 60 日线、动量排名靠后？说明依据和不确定性。
2.【观察名单】哪些板块动量靠前且站上 60 日线？还缺什么确认条件。
3.【仓位意见】整体是否需要调整仓位？如果信号互相矛盾，请直接指出矛盾，不要强行给结论。
"""


def call_llm(prompt):
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print("未设置环境变量 DEEPSEEK_API_KEY，跳过模型诊断。")
        return None
    payload = {
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": "你是专业的量化交易员，回答干练，只给定性建议，不计算买卖股数，"
                                          "对没有验证过的信号要明确说明不确定性。"},
            {"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    try:
        resp = requests.post("https://api.deepseek.com/chat/completions",
                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                             json=payload, timeout=90).json()
        if "choices" not in resp:
            print(f"模型返回异常: {str(resp)[:200]}")
            return None
        return resp["choices"][0]["message"]["content"]
    except Exception as e:  # noqa: BLE001
        print(f"调用失败: {e!r}")
        return None


def save_log(prompt, reply):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"decision_{datetime.now():%Y%m%d}.txt")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n===== {datetime.now():%H:%M:%S} PROMPT =====\n{prompt}\n===== REPLY =====\n{reply}\n")
    print(f"(已记录到 {path})")


def trade_loop(portfolio):
    print("\n💡 指令: buy 代码 数量 价格 / sell 代码 数量 价格 / exit 退出")
    while True:
        cmd = input("请输入指令: ").strip().split()
        if not cmd:
            continue
        if cmd[0].lower() == "exit":
            print("已退出，持仓文件保留最新状态。")
            return
        try:
            action, code, shares, price = cmd[0].lower(), cmd[1], int(cmd[2]), float(cmd[3])
            assert len(cmd) == 4 and action in ("buy", "sell") and shares > 0 and price > 0
        except Exception:  # noqa: BLE001
            print("❌ 格式错误，请重新输入。")
            continue

        holdings = portfolio["holdings"]
        amount = shares * price
        if action == "buy":
            if portfolio["cash"] < amount:
                print("❌ 现金不足！")
                continue
            h = holdings.setdefault(code, {"name": ETF_POOL.get(code, code), "shares": 0, "cost": 0})
            new_shares = h["shares"] + shares
            h["cost"] = round((h["shares"] * h["cost"] + amount) / new_shares, 3)
            h["shares"] = new_shares
            portfolio["cash"] -= amount
            print(f"✅ 买入: {code} {shares}份，花费 {amount:.0f}元，现金剩余 {portfolio['cash']:.0f}元")
        else:
            if code not in holdings or holdings[code]["shares"] < shares:
                print("❌ 持仓份额不足！")
                continue
            holdings[code]["shares"] -= shares
            portfolio["cash"] += amount
            print(f"✅ 卖出: {code} {shares}份，回笼 {amount:.0f}元，现金剩余 {portfolio['cash']:.0f}元")
            if holdings[code]["shares"] == 0:
                del holdings[code]
        with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)


def main():
    if not os.path.exists(PORTFOLIO_FILE):
        print(f"找不到 {PORTFOLIO_FILE}，请先创建！")
        return
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
        portfolio = json.load(f)

    print(f"[{datetime.now():%H:%M:%S}] 正在拉取历史数据与实时盘口...")
    realtime = get_tx_realtime(list(ETF_POOL) + [c for c in portfolio["holdings"] if c not in ETF_POOL])
    if not realtime:
        print("实时行情为空，终止（请先检查网络或接口）。")
        return
    results = build_table(realtime)
    price_dict = {c: v["price"] for c, v in realtime.items()}
    assets, pos_pct, holdings_str = value_portfolio(portfolio, price_dict)
    print_dashboard(results, assets, pos_pct)

    prompt = build_prompt(holdings_str, assets, portfolio["cash"], pos_pct, results)
    print("正在呼叫 DeepSeek 进行诊断...")
    reply = call_llm(prompt)
    if reply:
        print("\n" + "★" * 30 + "\n🤖 DeepSeek 诊断意见：\n" + reply + "\n" + "★" * 30)
        save_log(prompt, reply)
    trade_loop(portfolio)


if __name__ == "__main__":
    main()
