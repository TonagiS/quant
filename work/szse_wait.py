"""深交所接口守候：被限流后每隔 60s 重试，通了就抓「ETF列表」全量并测历史日期参数。

用法：venv\\Scripts\\python.exe work\\szse_wait.py
日志：work\\szse_wait.log
"""

from __future__ import annotations

import csv
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "national_team" / "szse_etf_list"
LOG = ROOT / "work" / "szse_wait.log"
H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://www.szse.cn/market/product/list/etfList/index.html",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}
BASE = (
    "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1945"
    "&TABKEY=tab1&PAGENO={page}&random=0.1{extra}"
)
TAG = re.compile(r"<[^>]+>")


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers=H)
    return json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace"))[0]


def try_once() -> bool:
    try:
        d = get(BASE.format(page=1, extra=""))
        log(f"OK page1 subname={d['metadata'].get('subname')} n={len(d['data'])}")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"fail {type(exc).__name__}: {str(exc)[:80]}")
        return False


def dump_all(tag: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    page = 1
    while True:
        d = get(BASE.format(page=page, extra=""))
        meta = d["metadata"]
        for r in d["data"]:
            rows.append(
                {
                    "date": meta.get("subname"),
                    "code": TAG.sub("", r.get("sys_key", "")).strip(),
                    "name": TAG.sub("", r.get("kzjcurl", "")).strip(),
                    "index_code": (r.get("nhzs") or "").strip(),
                    "scale": r.get("dqgm", "").replace(",", ""),
                    "fund": r.get("glrmc", ""),
                }
            )
        if page >= int(meta.get("pagecount") or 1):
            break
        page += 1
        time.sleep(1.2)  # 客气点，别再把人家惹毛
    path = OUT / f"etf_list_{tag}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "code", "name", "index_code", "scale", "fund"])
        w.writeheader()
        w.writerows(rows)
    log(f"DONE {len(rows)} 行 -> {path}")


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 25 * 60
    while time.time() < deadline:
        if try_once():
            dump_all(time.strftime("%Y%m%d_%H%M"))
            # 蹭着能用，顺手测历史参数
            for d in ("2026-11-19", "2026-11-18"):
                try:
                    r = get(BASE.format(page=1, extra=f"&txtDate={d}"))
                    log(f"txtDate={d} -> subname={r['metadata'].get('subname')}")
                except Exception as exc:  # noqa: BLE001
                    log(f"txtDate={d} fail {str(exc)[:60]}")
                time.sleep(2)
            return
        time.sleep(60)
    log("TIMEOUT 25 分钟都没通，放弃")


if __name__ == "__main__":
    main()
