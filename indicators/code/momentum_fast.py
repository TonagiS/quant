import akshare as ak
import pandas as pd
import numpy as np

# ==========================================
# 配置区：你的细分赛道关注池
# ==========================================
etf_pool = {
    # --- 宽基底仓 ---
    "sh510300": "沪深300", "sh510500": "中证500", "sh588000": "科创50", "sz159915": "创业板",
    # --- AI 硬件/算力 (CPO + PCB + 芯片) ---
    "sh515050": "通信CPO/PCB",   # CPO + PCB 综合
    "sh515320": "电子50(PCB)",   # PCB 含量较高
    "sh588810": "科创芯片",       # 科创板芯片设计
    "sh159516": "半导体设备",     # 半导体材料设备
    # --- AI 软件/应用 ---
    "sh515070": "人工智能AI",    # AI 应用 + 算力
    "sh512930": "AI人工智能",    # 另一只 AI 主题
    # --- 其他你原有持仓（示例，可自行增删）---
    "sh512480": "半导体", "sh512660": "军工", "sh518880": "黄金",
    "sh512010": "医药", "sh512880": "证券", "sh512800": "银行",
    "sh515220": "煤炭", "sh512400": "有色金属",
}

# 动量周期参数（可调）
MOM_SHORT = 20   # 短期动量（约1个月）
MOM_LONG = 60    # 中期动量（约3个月）

end_date = pd.Timestamp.now().strftime("%Y%m%d")
start_date = (pd.Timestamp.now() - pd.Timedelta(days=300)).strftime("%Y%m%d")

results = []
print(f"正在拉取 {len(etf_pool)} 只ETF的灵敏动量数据...")

for code, name in etf_pool.items():
    try:
        df = ak.stock_zh_a_hist_tx(symbol=code, start_date=start_date, end_date=end_date, adjust="qfq")
        if df is None or len(df) < MOM_LONG:
            continue
        df = df.sort_values("date").reset_index(drop=True)
        close = pd.to_numeric(df["close"], errors="coerce").dropna()
        if len(close) < MOM_LONG:
            continue

        current_price = close.iloc[-1]
        
        # 短期动量（20日）
        mom_short = (current_price / close.iloc[-MOM_SHORT] - 1) * 100 if len(close) >= MOM_SHORT else np.nan
        # 中期动量（60日）
        mom_long = (current_price / close.iloc[-MOM_LONG] - 1) * 100 if len(close) >= MOM_LONG else np.nan
        
        # 趋势过滤：是否在 20 日和 60 日均线上方
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        above_ma20 = current_price > ma20 if not np.isnan(ma20) else False
        above_ma60 = current_price > ma60 if not np.isnan(ma60) else False

        results.append({
            "名称": name, "代码": code, "最新价": round(current_price, 3),
            "1月动量": round(mom_short, 2) if not np.isnan(mom_short) else None,
            "3月动量": round(mom_long, 2) if not np.isnan(mom_long) else None,
            ">20日线": "✅" if above_ma20 else "❌",
            ">60日线": "✅" if above_ma60 else "❌",
        })
    except Exception:
        continue

if not results:
    print("没有获取到数据，请检查网络或ETF代码。")
    exit()

# 按 1月动量 排序（灵敏模式）
results.sort(key=lambda x: (x["1月动量"] is not None, x["1月动量"]), reverse=True)

print("\n" + "=" * 80)
print(f"{'灵敏动量雷达（按1月动量排序）':^60}")
print("=" * 80)
print(f"{'板块':<12} {'1月动量':<10} {'3月动量':<10} {'>20日':<6} {'>60日':<6} {'最新价'}")
print("-" * 80)
for r in results:
    m1 = f"{r['1月动量']}%" if r["1月动量"] is not None else "N/A"
    m3 = f"{r['3月动量']}%" if r["3月动量"] is not None else "N/A"
    print(f"{r['名称']:<12} {m1:<10} {m3:<10} {r['>20日线']:<6} {r['>60日线']:<6} {r['最新价']}")

print("=" * 80)
print("💡 筛选逻辑：1月动量 > 0 且 站稳20日线，代表短期强势；")
print("💡 共振信号：1月动量、3月动量均为正，且同时站上20日、60日线，趋势最健康。")
print("💡 你可以直接修改代码顶部的 MOM_SHORT / MOM_LONG 来调整灵敏度。")