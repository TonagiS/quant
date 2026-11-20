#!/usr/bin/env python3
"""slowdecline：贪心入选组合在 5/20bp 下复核（输出 greedy_picks_eval.csv）。"""
import sys;sys.path.insert(0,'work');import slowdecline as S,pandas as pd,numpy as np
S.setup();RC=S.RC
cands=[("S4+CREDIT",("OFF02","CNY_1P","PMI50","TS10"),0.6),("S2+CREDIT",("OFF02","CNY_1P","PMI50","TS12"),0.6),
 ("S4+CREDIT",("CNY_1P","TS12"),0.6),("S4+CREDIT",("OFF03","TS12"),0.6),("S4+CREDIT",("OFF03","TS12"),0.5),("S4+CREDIT",(),0.6),
 ("S2+CREDIT",("TS08","CNY_1P","OFF03","AM120_0"),0.0),("S4",("EWT60_0","TERM_MED","OFF02","TS10"),0.6),("S4+CREDIT",("TS12",),0.5),("S4+CREDIT",("CNY_1P",),0.5)]
rows=[]
for base,arms,wc in cands:
  for cost in (0.0005,0.002):
    for w in RC.START_W:
      b=S.BASES[base]; d=S.sat(w,b["sector"],b["credit"],S.merge_arms(list(arms)),cost)
      if wc: d=RC.mix(S.I.core("C3",d["lo"],cost),d,wc,d["lo"],cost)
      nm=(f"C3{int(wc*100)}/" if wc else "")+base+"".join("+"+a for a in arms)
      for r in S.perf_rows(d,w,{"combo":nm,"cost_bp":int(cost*1e4)}): rows.append(r)
t=pd.DataFrame(rows); t.to_csv(S.RUN/"greedy_picks_eval.csv",index=False)
x=t[t.window.isin(list(RC.START_W))].pivot_table(index=["combo","cost_bp"],columns="window",values="calmar")
y=t[t.window=="W2013"].set_index(["combo","cost_bp"])[["cagr","mdd","SD300_cum"]]
z=t.pivot_table(index=["combo","cost_bp"],columns="window",values="mdd")[["Y2015","Y2018","S2021_24","W2017","W2019"]]
pd.set_option('display.width',250); print(pd.concat([x,y,z],axis=1).round(3).to_string())
