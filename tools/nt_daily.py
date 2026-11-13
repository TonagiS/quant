"""每日快照：沪市宽基 ETF 份额 + 上证指数收盘。

数据口径
--------
* ETF 份额：上交所官方接口（COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L），
  返回 STAT_DATE / SEC_CODE / SEC_NAME / TOT_VOL。TOT_VOL 单位是「万份」，
  本脚本同时存万份和份。
* 指数：新浪 hq 接口取上证综指收盘（收盘后跑，取到的就是当日收盘价）。
* 深市 ETF（159919 等）：深交所接口对非浏览器请求返回 403，暂时不覆盖；
  沪深300 用 510300 / 510310 / 510330 三只沪市 ETF 代表，覆盖度足够。

输出
----
data/national_team/etf_shares.csv   date,code,name,shares_wan,shares
data/national_team/index_close.csv  date,code,close
data/national_team/latest.md        当日摘要（含与上一交易日的份额变动）

用法
----
venv\\Scripts\\python.exe tools\\nt_daily.py
"""

from __future__ import annotations

import csv
import json
import os
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "national_team"
SHARES_CSV = OUT / "etf_shares.csv"
INDEX_CSV = OUT / "index_close.csv"
LATEST_MD = OUT / "latest.md"

# 宽基（沪市）：代码 -> 简称
WIDE = {
    "510050": "上证50ETF",
    "510300": "沪深300ETF(华泰柏瑞)",
    "510310": "沪深300ETF(易方达)",
    "510330": "沪深300ETF(华夏)",
    "510500": "中证500ETF(南方)",
    "512500": "中证500ETF(华夏)",
    "512100": "中证1000ETF(南方)",
    "588000": "科创50ETF(华夏)",
    "588080": "科创板50ETF(易方达)",
}

SSE_URL = (
    "http://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
    "&pageHelp.pageSize=3000&pageHelp.pageNo=1&pageHelp.beginPage=1&pageHelp.cacheSize=1&pageHelp.endPage=1"
)
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://www.sse.com.cn/"}


def fetch_sse_shares() -> tuple[str, dict[str, float]]:
    req = urllib.request.Request(SSE_URL, headers=HEADERS)
    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))
    rows = data.get("result") or []
    stat_date, out = "", {}
    for row in rows:
        code = str(row.get("SEC_CODE") or "").strip()
        if code not in WIDE:
            continue
        try:
            shares_wan = float(row.get("TOT_VOL") or 0)
        except (TypeError, ValueError):
            continue
        stat_date = stat_date or str(row.get("STAT_DATE") or "").strip()
        out[code] = shares_wan
    return stat_date, out


def fetch_index_close() -> dict[str, float]:
    url = "https://hq.sinajs.cn/list=sh000001,sh000300,sh000905"
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    )
    body = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "replace")
    out: dict[str, float] = {}
    for line in body.splitlines():
        if "=" not in line:
            continue
        head, _, rest = line.partition("=")
        code = head.split("_")[-1].strip()
        fields = rest.strip().strip(";").strip('"').split(",")
        if len(fields) < 4:
            continue
        try:
            out[code] = float(fields[3])  # 当前价（收盘后即收盘价）
        except ValueError:
            continue
    return out


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def append_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stat_date, shares = fetch_sse_shares()
    stat_date = stat_date or date.today().isoformat()
    index_close = fetch_index_close()

    # 份额
    old = read_csv(SHARES_CSV)
    old_dates = sorted({r["date"] for r in old})
    existing = {(r["date"], r["code"]) for r in old}
    new_rows = []
    for code, shares_wan in sorted(shares.items()):
        if (stat_date, code) in existing:
            continue
        new_rows.append(
            {
                "date": stat_date,
                "code": code,
                "name": WIDE[code],
                "shares_wan": round(shares_wan, 2),
                "shares": int(round(shares_wan * 10000)),
            }
        )
    append_rows(SHARES_CSV, ["date", "code", "name", "shares_wan", "shares"], new_rows)

    # 指数
    old_idx = read_csv(INDEX_CSV)
    existing_idx = {(r["date"], r["code"]) for r in old_idx}
    idx_rows = [
        {"date": stat_date, "code": code, "close": round(close, 3)}
        for code, close in sorted(index_close.items())
        if (stat_date, code) not in existing_idx
    ]
    append_rows(INDEX_CSV, ["date", "code", "close"], idx_rows)

    # 与上一交易日对比
    rows = read_csv(SHARES_CSV)
    by_date: dict[str, dict[str, float]] = {}
    for r in rows:
        by_date.setdefault(r["date"], {})[r["code"]] = float(r["shares_wan"])
    dates = sorted(by_date)
    prev = by_date.get(dates[-2], {}) if len(dates) >= 2 else {}
    cur = by_date.get(dates[-1], {}) if dates else {}
    lines = [
        f"# 国家队通道快照 {stat_date}",
        "",
        f"- 数据源：上交所 ETF 份额接口；指数：新浪",
        f"- 快照天数：{len(dates)}（{dates[0] if dates else '-'} ~ {dates[-1] if dates else '-'}）",
        "",
        "| 代码 | 名称 | 份额(亿份) | 较上一交易日(亿份) |",
        "| --- | --- | --- | --- |",
    ]
    for code in sorted(cur):
        now = cur[code] / 10000.0
        before = prev.get(code)
        delta = f"{now - before / 10000.0:+.2f}" if before else "-"
        lines.append(f"| {code} | {WIDE.get(code, code)} | {now:.2f} | {delta} |")
    lines.append("")
    if index_close:
        lines.append("指数收盘：" + "，".join(f"{k}={v:.2f}" for k, v in sorted(index_close.items())))
    LATEST_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[ok] {stat_date} 份额 {len(shares)} 只，新增 {len(new_rows)} 行；指数 {sorted(index_close)}")
    print(f"[ok] 写 {SHARES_CSV.name} / {INDEX_CSV.name} / {LATEST_MD.name}")

    # 顺手并进长历史（供信号研究／影子日志），并按冻结参数记一行影子信号（只记录不交易）
    try:
        hist = OUT / "etf_shares_hist.csv"
        have = set()
        if hist.exists():
            with hist.open(encoding="utf-8-sig", newline="") as fh:
                have = {r["date"] for r in csv.DictReader(fh)}
        if new_rows and stat_date not in have:
            append_rows(hist, ["date", "code", "name", "shares_wan"], new_rows)
            print(f"[ok] 合并进 {hist.name}（{stat_date}）")
    except Exception as exc:  # 历史合并失败不影响每日快照
        print(f"[warn] 历史合并失败：{exc}")

    try:
        from nt_signal_shadow import main as shadow_main

        shadow_main([])
    except SystemExit:
        pass
    except Exception as exc:
        print(f"[warn] 影子信号记录失败：{exc}")


if __name__ == "__main__":
    main()
