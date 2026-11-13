"""牛市/熊市分解：单标的上 VT15 为什么输给 60日线清仓（探索性，非预登记）。"""
import sys, glob, os
from pathlib import Path
import numpy as np, pandas as pd
ROOT=Path(r"E:\Code\quant")
TD=252; COST=0.0005

def load(p):
    df=pd.read_csv(p,parse_dates=["date"])
    s=df.set_index("date")["close"].astype(float)
    return s[~s.index.duplicated(keep="last")].sort_index()

def pos_vt(ret,target=.15,win=60,reb=5):
    vol=ret.rolling(win,min_periods=40).std()*np.sqrt(TD)
    raw=(target/vol).clip(0,1)
    mask=np.arange(len(raw))%reb==0
    return raw.where(mask).ffill().fillna(0.0)

def pos_ma(close,win=60):
    ma=close.rolling(win,min_periods=20).mean()
    return pd.Series(np.where(close>ma,1.0,0.0),index=close.index)

def net(pos,ret):
    p=pos.shift(1).fillna(0.0)
    return p*ret.fillna(0.0)-(p.diff().abs().fillna(p.abs()))*COST, p

def cagr(r):
    r=np.asarray(r,float); n=len(r)
    return np.cumprod(1+r)[-1]**(TD/n)-1 if n>60 else np.nan
def calmar(r):
    r=np.asarray(r,float); eq=np.cumprod(1+r); dd=(eq/np.maximum.accumulate(eq)-1).min()
    return cagr(r)/abs(dd) if dd<0 else np.nan

hs=load(ROOT/"data/cache2019/sh510300.csv")
bull=(hs>hs.rolling(200,min_periods=100).mean()).reindex(hs.index)

rows=[]
for p in sorted(glob.glob(str(ROOT/"data/cache2019/*.csv"))):
    code=os.path.basename(p)[:-4]
    if code in ("sh510300","000300"): continue
    c=load(p)
    if len(c)<300: continue
    ret=c.pct_change()
    b=bull.reindex(c.index).ffill().fillna(False).values.astype(bool)
    pv=pos_vt(ret); pm=pos_ma(c)
    nv,ev=net(pv,ret); nm,em=net(pm,ret); nf,ef=net(pd.Series(1.0,index=c.index),ret)
    m=ev.notna().values&ef.notna().values
    if m.sum()<250: continue
    rows.append(dict(group="ETF",name=code,
        bull_share=b[m].mean(),
        vt_pos_bull=ev.values[m][b[m]].mean(), vt_pos_bear=ev.values[m][~b[m]].mean(),
        ma_pos_bull=em.values[m][b[m]].mean(), ma_pos_bear=em.values[m][~b[m]].mean(),
        cagr_f=cagr(nf.values[m]), cagr_v=cagr(nv.values[m]), cagr_m=cagr(nm.values[m]),
        cal_f=calmar(nf.values[m]), cal_v=calmar(nv.values[m]), cal_m=calmar(nm.values[m]),
        bull_er_f=np.prod(1+nf.values[m][b[m]])-1, bull_er_v=np.prod(1+nv.values[m][b[m]])-1,
        bull_er_m=np.prod(1+nm.values[m][b[m]])-1))
df=pd.DataFrame(rows)
for g in ["ETF"]:
    s=df[df.group==g]
    print(f"== {g} n={len(s)}")
    print(f"牛市日占比中位 {s.bull_share.median():.0%}")
    print(f"牛市日均敞口：VT15 {s.vt_pos_bull.median():.2f}   MA60清仓 {s.ma_pos_bull.median():.2f}   满仓 1.00")
    print(f"熊市日均敞口：VT15 {s.vt_pos_bear.median():.2f}   MA60清仓 {s.ma_pos_bear.median():.2f}")
    print(f"年化中位：满仓 {s.cagr_f.median():.1%}  VT15 {s.cagr_v.median():.1%}  MA60 {s.cagr_m.median():.1%}")
    print(f"卡玛中位：满仓 {s.cal_f.median():.3f}  VT15 {s.cal_v.median():.3f}  MA60 {s.cal_m.median():.3f}")
    print(f"牛市累计收益中位：满仓 {s.bull_er_f.median():.0%}  VT15 {s.bull_er_v.median():.0%}  MA60 {s.bull_er_m.median():.0%}")
    print(f"牛市捕获率中位：VT15 {(s.bull_er_v/s.bull_er_f).median():.0%}  MA60 {(s.bull_er_m/s.bull_er_f).median():.0%}")
df.to_csv(ROOT/"work/bull_decomp_20261114.csv",index=False,encoding="utf-8-sig")
