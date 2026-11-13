"""抓 B 站 UP 主「梦想超过余额宝」的每日追踪投稿标题，解析增持/减持标签。

数据源：合集「A股国家队数据追踪」(season_id 7828884)，用 polymer 接口翻页，
不需要 wbi 签名（/x/space/wbi/arc/search 已被风控，返回 -352）。

输出 runs/<日期>_bili_wangwang/titles.csv
字段：pub_date,pub_time,bvid,title,label,pct,data_date,tag,duration
  label     待增持 / 增持 / 待减持 / 减持 / 其他（按标题引号内文字提取）
  pct       标题里的百分比数字
  data_date 标题里"X月X日…公示/披露"的日期（数据日）
  tag       标题前缀【急】【不急】【深度】等

用法：venv\\Scripts\\python.exe tools\\bili_nt_titles.py
"""

from __future__ import annotations

import csv
import re
import time
from collections import Counter
from datetime import date
from pathlib import Path

import requests

MID = 32222710
SEASON_ID = 7828884
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / f"{date.today():%Y%m%d}_bili_wangwang"
CSV_PATH = OUT / "titles.csv"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
ARCHIVE_URL = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"

LABELS = ("待增持", "待减持", "增持", "减持", "持平")
TAG_RE = re.compile(r"^【([^】]+)】")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")
QUOTE_RE = re.compile(r"[“\"「『]([^”\"」』]{2,24}?)[”\"」』]")


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": f"https://space.bilibili.com/{MID}"})
    return s


def fetch_archives(s: requests.Session, page_size: int = 30) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        p = {
            "mid": MID,
            "season_id": SEASON_ID,
            "page_num": page,
            "page_size": page_size,
            "sort_reverse": "false",
        }
        d = s.get(ARCHIVE_URL, params=p, timeout=25).json()
        if d.get("code") != 0:
            raise RuntimeError(f"page {page}: {d.get('code')} {d.get('message')}")
        data = d["data"]
        arch = data.get("archives") or []
        out.extend(arch)
        total = data["page"]["total"]
        if len(out) >= total or not arch:
            break
        page += 1
        time.sleep(1.2)
    return out


def parse(title: str, created: int) -> dict:
    pub = time.strftime("%Y-%m-%d %H:%M", time.localtime(created))
    m = TAG_RE.match(title)
    tag = m.group(1) if m else ""

    quote = QUOTE_RE.search(title)
    scan = quote.group(1) if quote else title

    label = next((c for c in LABELS if c in scan), "")
    if not label:
        label = next((c for c in LABELS if c in title), "")

    pm = PCT_RE.search(scan) or PCT_RE.search(title)
    pct = pm.group(1) if pm else ""

    data_date = ""
    dm = DATE_RE.search(title)
    if dm:
        mon, day = int(dm.group(1)), int(dm.group(2))
        year = time.localtime(created).tm_year
        if mon > time.localtime(created).tm_mon + 1:  # 跨年保护
            year -= 1
        data_date = f"{year:04d}-{mon:02d}-{day:02d}"

    return {
        "pub_date": pub[:10],
        "pub_time": pub,
        "bvid": "",
        "title": title,
        "label": label,
        "pct": pct,
        "data_date": data_date,
        "tag": tag,
        "duration": "",
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    s = session()
    arch = fetch_archives(s)
    print(f"[ok] 合集 archive {len(arch)} 条")

    rows = []
    for a in arch:
        r = parse(a["title"], a["pubdate"])
        r["bvid"] = a["bvid"]
        r["duration"] = a.get("duration", "")
        rows.append(r)
    rows.sort(key=lambda r: r["pub_time"])

    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["pub_date", "pub_time", "bvid", "title", "label", "pct", "data_date", "tag", "duration"],
        )
        w.writeheader()
        w.writerows(rows)

    print("[labels]", Counter(r["label"] for r in rows))
    print("[tags]", Counter(r["tag"] for r in rows))
    print("[range]", rows[0]["pub_date"], "~", rows[-1]["pub_date"])
    print("[out]", CSV_PATH)


if __name__ == "__main__":
    main()
