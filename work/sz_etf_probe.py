"""深交所 ETF 列表接口探查：能否取到深市宽基 ETF 的份额（当前规模）+ 是否支持历史日期。

用法：venv\\Scripts\\python.exe work\\sz_etf_probe.py [日期 YYYY-MM-DD]
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "national_team"
H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://www.szse.cn/",
}
BASE = (
    "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1945"
    "&TABKEY=tab1&PAGENO={page}&random=0.1{extra}"
)
TAG = re.compile(r"<[^>]+>")


def get_json(url: str, tries: int = 4) -> dict:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=H)
            body = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
            return json.loads(body)[0]
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.0 + i)
    raise RuntimeError(f"fetch failed: {last}")


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    extra = f"&txtDate={target}" if target else ""
    first = get_json(BASE.format(page=1, extra=extra))
    meta = first["metadata"]
    header = re.sub(r"\s+", " ", TAG.sub(" ", meta.get("header") or "")).strip()
    print("[meta] 报告名:", meta.get("name"), "| 数据日期(subname):", meta.get("subname"))
    print("[meta] 记录数:", meta.get("recordcount"), "| 页数:", meta.get("pagecount"))
    print("[header]", header[:400])
    print("[row0]", json.dumps(first["data"][0], ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
