#!/usr/bin/env python3
"""研报 PDF 前 2 页文本抓取 + 盈利预测（EPS）解析。
免费接口的结构化 EPS 字段 2024 年以前基本为空，只能从 PDF（pdf.dfcfw.com，公开）里读预测表。
文本缓存 data/cache_analyst/txt/{yyyymm}/{infoCode}.txt.gz（gitignored）。"""
from __future__ import annotations
import gzip, os, re, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd, requests

ROOT = Path(__file__).resolve().parents[1]
TXT = ROOT / "data" / "cache_analyst" / "txt"
import threading
_TL = threading.local()


def _sess():
    if not hasattr(_TL, "s"):
        _TL.s = requests.Session()
        _TL.s.headers["User-Agent"] = "Mozilla/5.0"
    return _TL.s


def _get(url, deadline=40):
    """curl 子进程 + 硬超时（requests 在该 CDN 上偶发长时间卡在 SSL 读）。"""
    try:
        r = subprocess.run(["curl", "-s", "-f", "-L", "--max-time", str(deadline), "-A", "Mozilla/5.0", url],
                           capture_output=True, timeout=deadline + 10)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def fetch_txt(info, ym):
    d = TXT / ym
    f = d / f"{info}.txt.gz"
    if f.exists():
        return gzip.open(f, "rt").read()
    b = None
    for k in range(3):
        try:
            c = _get(f"https://pdf.dfcfw.com/pdf/H3_{info}_1.pdf")
            if c is not None and c[:4] == b"%PDF":
                b = c
                break
        except Exception:
            pass
        time.sleep(1 + k)
    if b is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf", dir="/tmp", delete=True) as tf:
        tf.write(b); tf.flush()
        try:
            t = subprocess.run(["pdftotext", "-layout", "-f", "1", "-l", "2", tf.name, "-"],
                               capture_output=True, text=True, timeout=60).stdout
        except Exception:
            t = ""
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(f, "wt") as g:
        g.write(t)
    return t


YR = re.compile(r"(20[0-3]\d)\s*([AEFPaefp]|年[EeAa]?)?(?![\d.])")
NUM = re.compile(r"(?<![\d.])-?\d+\.\d+(?![\d.])|(?<![\d.])-?\d+(?![\d.%])")
EPS_LBL = re.compile(r"(EPS|每股收益|每股盈利|每股净?收益|每股盈余)", re.I)
BAD_LBL = re.compile(r"(增长|同比|增速|变动|growth|yoy|%|摊薄前|PE|市盈)", re.I)
TXT_PAT = re.compile(
    r"(20[1-3]\d)\s*[-–—~至、/]\s*(?:(20[1-3]\d)\s*[-–—~至、/]\s*)?(20[1-3]\d)?\s*年?(?:的|公司)?[^。；\n]{0,30}?"
    r"(?:EPS|每股收益)[^。；\n\d]{0,12}?((?:-?\d+\.\d+\s*(?:元|元/股)?\s*[、,，和及/]\s*){1,3}-?\d+\.\d+)", re.I)


TXT2 = re.compile(r"(?:EPS|每股收益)(?:\(元\))?(?:分别)?(?:为|是|达到|:|：)((?:-?\d+\.\d+(?:元/股|元)?[、,，和及/]){1,3}-?\d+\.\d+)", re.I)


def _years(line):
    ys = []
    for m in YR.finditer(line):
        y = int(m.group(1))
        if 2010 <= y <= 2035:
            ys.append((y, (m.group(2) or "").upper()))
    return ys


def parse_eps(t, pub_year):
    """返回 {fiscal_year: eps}（只取预测年份 >= pub_year-1）。先表格、后正文。"""
    if not t:
        return {}, "none"
    lines = t.splitlines()
    best = {}
    for i, ln in enumerate(lines):
        ys = _years(ln)
        # 表头：至少 3 个连续递增年份
        if len(ys) < 3:
            continue
        yv = [y for y, _ in ys]
        run = [yv[0]]
        for y in yv[1:]:
            if y == run[-1] + 1:
                run.append(y)
            elif len(run) < 3:
                run = [y]
            else:
                break
        if len(run) < 3:
            continue
        for ln2 in lines[i + 1: i + 30]:
            m = EPS_LBL.search(ln2)
            if not m:
                continue
            lbl = ln2[m.start(): m.end() + 12]
            if BAD_LBL.search(lbl):
                continue
            nums = [float(x) for x in NUM.findall(ln2[m.end():])]
            nums = [x for x in nums if abs(x) < 500]
            if len(nums) < len(run):
                if len(nums) >= 2 and len(nums) == len(run) - 1:
                    pass
                else:
                    continue
            vals = nums[: len(run)]
            out = {y: v for y, v in zip(run, vals)}
            out = {y: v for y, v in out.items() if y >= pub_year - 1}
            if len(out) >= 2:
                return out, "table"
            break
    body = re.sub(r"\s+", "", t)
    for m in TXT2.finditer(body):
        vals = [float(x) for x in re.findall(r"-?\d+\.\d+", m.group(1))]
        back = body[max(0, m.start() - 120): m.start()]
        yrs = []
        rg = list(re.finditer(r"(20[1-3]\d)年?[-–—~至](20[1-3]\d)", back))
        if rg:
            a, b = int(rg[-1].group(1)), int(rg[-1].group(2))
            yrs = list(range(a, b + 1)) if 0 < b - a <= 4 else []
        if not yrs:
            ys = [int(x) for x in re.findall(r"(20[1-3]\d)(?=年|E|e)", back)]
            ys = sorted(set(y for y in ys if y >= pub_year - 1))
            yrs = ys[-len(vals):] if len(ys) >= len(vals) else []
        if len(yrs) == len(vals) and all(abs(v) < 500 for v in vals) and yrs == list(range(yrs[0], yrs[0] + len(yrs))):
            out = {y: v for y, v in zip(yrs, vals) if y >= pub_year - 1}
            if out:
                return out, "text"
    return {}, "fail"


def run_month(ym, codes=None, workers=16):
    meta = pd.read_csv(ROOT / "data/cache_analyst/meta" / f"{ym}.csv.gz", dtype=str)
    if codes is not None:
        meta = meta[meta.stockCode.isin(codes)]
    meta = meta.drop_duplicates("infoCode")

    def one(row):
        t = fetch_txt(row.infoCode, ym.replace("-", ""))
        py = int(row.publishDate[:4])
        eps, how = parse_eps(t, py)
        return [dict(infoCode=row.infoCode, how=how, fy=y, eps=v) for y, v in eps.items()] or \
               [dict(infoCode=row.infoCode, how=how, fy=None, eps=None)]
    rows = []
    with ThreadPoolExecutor(workers) as ex:
        for r in ex.map(one, meta.itertuples()):
            rows += r
    return pd.DataFrame(rows)


if __name__ == "__main__":
    pass
