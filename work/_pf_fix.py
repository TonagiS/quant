import sys
sys.path.insert(0,r"E:\Code\quant"); sys.path.insert(0,r"E:\Code\quant\work")
import numpy as np, pandas as pd
from position.engine import Config, VolTarget, features, load_panel, simulate
import weekly_ma30ma60_test as T
close=load_panel("ext"); f=features(close); start=f["start"]; dates=close.index
ret=np.nan_to_num(f["ret"].to_numpy(),nan=0.0); cols=list(close.columns); idx={c:j for j,c in enumerate(cols)}
cfg=Config(reb=5,k=5,delay=1,cost=T.COST,cap=1.0,rule=VolTarget(0.15,every=1))
res=simulate(close,f,cfg,keep_weights=True); wh=res["w_hist"]; Tn=len(close)
base=res["ret"]; eq=np.cumprod(1+base[start:]); dd=(eq/np.maximum.accumulate(eq)-1).min()
print("engine 自报基线: cagr %.3f mdd %.3f calmar %.3f"%(eq[-1]**(252/len(base[start:]))-1,dd,(eq[-1]**(252/len(base[start:]))-1)/abs(dd)))
mult={}
for tag,kind in [("MA30半+MA60清","U0"),("周线A","U1")]:
    m={}
    for c in cols:
        pos=T.ladder_pos(close[c].dropna(),kind).reindex(dates).ffill().fillna(1.0)
        m[c]=pos.shift(1).fillna(1.0).to_numpy()
    mult[tag]=m
def run(tag,use):
    port=np.zeros(Tn); prev={}
    for i in range(start+1,Tn):
        w={cols[c]:v for c,v in wh[i-1].items()}   # 用昨日收盘后的权重赚今日收益
        new={}
        for c,v in w.items():
            vv=v*(mult[tag][c][i-1] if use else 1.0)
            if vv>1e-9: new[c]=vv
        g=sum(new.get(c,0.0)*ret[i,idx[c]] for c in new)
        tn=sum(abs(new.get(c,0.0)-prev.get(c,0.0)) for c in set(new)|set(prev))
        port[i]=g-tn*T.COST; prev=new
    r=port[start+1:]; eq2=np.cumprod(1+r); dd2=(eq2/np.maximum.accumulate(eq2)-1).min(); cg=eq2[-1]**(252/len(r))-1
    print(f"{tag:<16} cagr {cg:.3f}  mdd {dd2:.3f}  calmar {cg/abs(dd2):.3f}  sharpe {r.mean()/r.std()*np.sqrt(252):.3f}")
run("基线(权重重算)",False)
run("MA30半+MA60清",True); run("周线A",True)
