import sys
sys.path.insert(0,r"E:\Code\quant"); sys.path.insert(0,r"E:\Code\quant\work")
import numpy as np, pandas as pd
from position.engine import Config, VolTarget, features, load_panel, simulate
import weekly_ma30ma60_test as T
close=load_panel("ext"); f=features(close); start=f["start"]; dates=close.index
ret=np.nan_to_num(f["ret"].to_numpy(),nan=0.0); cols=list(close.columns); idx={c:j for j,c in enumerate(cols)}
for every in (1,5):
    cfg=Config(reb=5,k=5,delay=1,cost=T.COST,cap=1.0,rule=VolTarget(target=0.15,window=60,every=every))
    res=simulate(close,f,cfg,keep_weights=True); wh=res["w_hist"]; Tn=len(close)
    mult={}
    for tag,kind in [("MA30半+MA60清","U0"),("周线A","U1")]:
        m={}
        for c in cols:
            pos=T.ladder_pos(close[c].dropna(),kind).reindex(dates).ffill().fillna(1.0)
            m[c]=pos.shift(1).fillna(1.0).to_numpy()
        mult[tag]=m
    def run(tag,use):
        port=np.zeros(Tn); prev={}; expo=[]
        for i in range(start,Tn):
            w={cols[c]:v for c,v in wh[i].items()}
            new={c:v*(mult[tag][c][i] if use else 1.0) for c,v in w.items() if v*(mult[tag][c][i] if use else 1.0)>1e-9}
            g=sum(new.get(c,0.0)*ret[i,idx[c]] for c in new)
            tn=sum(abs(new.get(c,0.0)-prev.get(c,0.0)) for c in set(new)|set(prev))
            port[i]=g-tn*T.COST; prev=new; expo.append(sum(new.values()))
        r=port[start:]; eq=np.cumprod(1+r); dd=(eq/np.maximum.accumulate(eq)-1).min()
        cagr=eq[-1]**(252/len(r))-1
        return dict(every=every,arm=tag,cagr=round(cagr,3),mdd=round(dd,3),calmar=round(cagr/abs(dd),3),sharpe=round(r.mean()/r.std()*np.sqrt(252),3),expo=round(np.mean(expo),3))
    print(run("基线",False)); print(run("MA30半+MA60清",True)); print(run("周线A",True))
