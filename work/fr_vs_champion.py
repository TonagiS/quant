#!/usr/bin/env python3
"""full_rebuild 事后补充（非预登记）：全部 4608 个配置对冠军用滚动起点判据。"""
import sys; sys.path.insert(0,'work'); import numpy as np, pandas as pd
from multiprocessing import Pool
import fr_rebuild as R
X=R.X
def init():
    R.setup()
def f(a):
    k,bp=a
    r=np.load(R.CACHE/f"ret_{bp}_{k.replace('|','__')}.npy").astype(float)
    lo=X.PV.G["lo0"]
    return k,bp,X.cmp(X.roll_vec(r,lo), CH[bp])
R.setup()
T=pd.read_csv("runs/2026-11-21_full_rebuild/grid_results.csv.gz")
CH={bp:X.roll_vec(np.load(R.CACHE/f"ret_{bp}_{R.key(R.CHAMP).replace('|','__')}.npy").astype(float),R.PV.G["lo0"]) for bp in (5,20)}
if __name__=="__main__":
    with Pool(8) as p: out=p.map(f,[(k,bp) for bp in (5,20) for k in T[T.cost_bp==bp].key],chunksize=50)
    D=pd.DataFrame([dict(key=k,cost_bp=bp,ch_share=a[0],ch_med=a[1],ch_seg=a[2],ch_pass=a[3]) for k,bp,a in out])
    D.to_csv("runs/2026-11-21_full_rebuild/posthoc_vs_champion.csv.gz",index=False)
    w=D.pivot_table(index="key",columns="cost_bp",values="ch_pass",aggfunc="first")
    print("pass5",int(w[5].sum()),"pass20",int(w[20].sum()),"both",int((w[5].astype(bool)&w[20].astype(bool)).sum()))
    print(D.sort_values("ch_med",ascending=False).groupby("cost_bp").head(5).round(3).to_string())
