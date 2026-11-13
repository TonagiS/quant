import glob,os
from pathlib import Path
import numpy as np, pandas as pd
ROOT=Path(r"E:\Code\quant"); TD=252; COST=0.0005
def load(p):
    df=pd.read_csv(p,parse_dates=["date"]); s=df.set_index("date")["close"].astype(float)
    return s[~s.index.duplicated(keep="last")].sort_index()
def pos_vt(ret,t=.15,w=60,reb=5):
    vol=ret.rolling(w,min_periods=40).std()*np.sqrt(TD); raw=(t/vol).clip(0,1)
    mask=np.arange(len(raw))%reb==0; return raw.where(mask).ffill().fillna(0.0)
def net(pos,ret):
    p=pos.shift(1).fillna(0.0); return p*ret.fillna(0.0)-(p.diff().abs().fillna(p.abs()))*COST,p
def calmar(r):
    r=np.asarray(r,float); eq=np.cumprod(1+r); dd=(eq/np.maximum.accumulate(eq)-1).min()
    return (eq[-1]**(TD/len(r))-1)/abs(dd) if dd<0 else np.nan
hs=load(ROOT/"data/cache2019/sh510300.csv")
bull=(hs>hs.rolling(200,min_periods=100).mean())
rows=[]
for p in sorted(glob.glob(str(ROOT/"data/cache2019/*.csv"))):
    c=load(p)
    if len(c)<300: continue
    ret=c.pct_change(); b=bull.reindex(c.index).ffill().fillna(False).values.astype(bool)
    vol=ret.rolling(60,min_periods=40).std()*np.sqrt(TD)
    pv=pos_vt(ret)
    nv,ev=net(pv,ret); nf,ef=net(pd.Series(1.0,index=c.index),ret)
    hy=pv.copy(); hy.iloc[b]=1.0; nhy,ehy=net(hy,ret)
    m=ev.notna().values&ef.notna().values
    if m.sum()<250: continue
    rows.append(dict(name=os.path.basename(p)[:-4],
        vol_bull=np.nanmedian(vol.values[m][b[m]]), vol_bear=np.nanmedian(vol.values[m][~b[m]]),
        full_share=np.nanmean((pv.values[m][b[m]]>=0.999)),
        cal_v=calmar(nv.values[m]), cal_hy=calmar(nhy.values[m]), cal_f=calmar(nf.values[m]),
        cagr_v=(np.prod(1+nv.values[m])**(TD/m.sum())-1), cagr_hy=(np.prod(1+nhy.values[m])**(TD/m.sum())-1)))
df=pd.DataFrame(rows)
print("n=",len(df))
print(f"牛市日 60日年化波动中位 {df.vol_bull.median():.0%}   熊市日 {df.vol_bear.median():.0%}")
print(f"牛市日里 VT15 给出满仓(敞口=1)的天数占比中位 {df.full_share.median():.0%}")
print(f"卡玛中位：满仓 {df.cal_f.median():.3f}  VT15 {df.cal_v.median():.3f}  混合(牛市满仓+VT15) {df.cal_hy.median():.3f}")
print(f"年化中位：VT15 {df.cagr_v.median():.1%}  混合 {df.cagr_hy.median():.1%}")
df.to_csv(ROOT/"work/bull_decomp2_20261114.csv",index=False,encoding="utf-8-sig")
