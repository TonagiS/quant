import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, r"E:\Code\quant")
from position.engine import Config, Fixed, MinRule, VolTarget, base_returns, features, simulate, slice_metrics
from position.valuation_light import valuation_pct, pctf
from position.val04_sellleg import load_panel, sell_leg, Series

pool="long+stocks"; COST=0.0005
close=load_panel(pool); f=features(close); start,T=f["start"],len(close); idx=close.index
f["base_ret"]=base_returns(close,f,Config(cost=COST))
pct=valuation_pct(idx)

def run(leg):
    cfg=Config(cost=COST, rule=MinRule([VolTarget(0.15,every=1), Series(leg,"SL")],"VT15xSL"))
    r=simulate(close,f,cfg); m=slice_metrics(r,start,T); return m

base=run(np.ones(T))
print("基线 VT15 年化%.2f%% 回撤%.1f%% 卡玛%.2f" % (base["cagr"]*100, base["mdd"]*100, base["calmar"]))
rows=[]
for shift,tag in ((0,"真信号"),(252,"+1年"),(-252,"-1年"),(504,"+2年"),(-504,"-2年")):
    p2=np.roll(pct,shift)
    m=run(sell_leg(p2,80.0,60,0.25,20,70.0))
    rows.append({"变体":tag,"年化":pctf(m["cagr"]),"回撤":pctf(m["mdd"]),"卡玛":round(m["calmar"],2),"仓位":f"{m['avg_expo']:.0%}"})
print(pd.DataFrame(rows).to_string(index=False))
