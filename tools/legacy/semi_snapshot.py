"""半导体三条线（设备/半导体/科创芯片）到 2026-11-04 收盘的状态快照。"""

from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path

ROOT = Path(r"E:\Code\quant")
CACHE = ROOT / "data" / "cache2019"
DAILY = ROOT / "data" / "daily"
OUT_DIR = ROOT / "runs" / f"{date.today():%Y%m%d}_semi_sector_snapshot"

TARGETS = [
    ("sz159516", "半导体设备"),
    ("sh512480", "半导体"),
    ("sh588810", "科创芯片"),
]
BENCH = ("sh510300", "沪深300")


def load_series(code: str) -> list[tuple[str, float, float | None]]:
    path = CACHE / f"{code}.csv"
    rows = []
    with path.open(encoding="utf-8-sig") as fh:
        header = fh.readline()
        for line in fh:
            parts = line.strip().split(",")
            if len(parts) < 2:
                continue
            try:
                d, c = parts[0], float(parts[1])
            except ValueError:
                continue
            amt = None
            if len(parts) > 2:
                try:
                    amt = float(parts[2])
                except ValueError:
                    amt = None
            rows.append((d, c, amt))
    return rows


def add_snapshots(rows: list[tuple[str, float, float | None]], code: str):
    seen = {r[0] for r in rows}
    for path in sorted(DAILY.glob("*.json")):
        ds = path.stem
        iso = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"
        if iso in seen:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        closes = data.get("codes") or {}
        close = closes.get(code)
        if close is None:
            continue
        rows.append((iso, float(close), None))
        seen.add(iso)
    rows.sort(key=lambda r: r[0])
    return rows


def pct(a: float, b: float) -> float:
    return a / b - 1.0


def analyze(rows, n_short=20, n_mid=60):
    closes = [r[1] for r in rows]
    amts = [r[2] for r in rows]
    last = closes[-1]
    out = {"close": last, "date": rows[-1][0], "bars": len(closes)}

    for label, n in (("r5", 5), ("r20", 20), ("r60", 60), ("r120", 120)):
        out[label] = pct(last, closes[-1 - n]) if len(closes) > n else None

    def ma(n):
        return sum(closes[-n:]) / n if len(closes) >= n else None

    out["ma20"] = ma(20)
    out["ma60"] = ma(60)
    out["ma120"] = ma(120)
    out["above_ma20"] = (last / out["ma20"] - 1) if out["ma20"] else None
    out["above_ma60"] = (last / out["ma60"] - 1) if out["ma60"] else None

    hi60 = max(closes[-60:])
    hi250 = max(closes[-250:]) if len(closes) >= 250 else max(closes)
    lo250 = min(closes[-250:]) if len(closes) >= 250 else min(closes)
    out["hi60"] = hi60
    out["dd60"] = last / hi60 - 1
    out["hi250"] = hi250
    out["dd250"] = last / hi250 - 1
    out["pos250"] = (last - lo250) / (hi250 - lo250) if hi250 > lo250 else None

    rets = [pct(closes[i], closes[i - 1]) for i in range(len(closes) - 60, len(closes))]
    out["vol60"] = statistics.pstdev(rets) * (252 ** 0.5) if rets else None

    peak = closes[-20]
    mdd = 0.0
    for c in closes[-20:]:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    out["mdd20"] = mdd

    valid_amt = [a for a in amts if a]
    if len(valid_amt) >= 20:
        ma5 = sum(valid_amt[-5:]) / 5
        ma20 = sum(valid_amt[-20:]) / 20
        out["amt_ratio"] = ma5 / ma20 if ma20 else None
    else:
        out["amt_ratio"] = None
    return out


def main() -> int:
    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    bench_rows = add_snapshots(load_series(BENCH[0]), BENCH[0])
    bench = analyze(bench_rows)

    results = []
    for code, name in TARGETS:
        rows = add_snapshots(load_series(code), code)
        st = analyze(rows)
        st["code"] = code
        st["name"] = name
        if st.get("r20") is not None and bench.get("r20") is not None:
            st["rs20"] = st["r20"] - bench["r20"]
        if st.get("r60") is not None and bench.get("r60") is not None:
            st["rs60"] = st["r60"] - bench["r60"]
        results.append(st)

    payload = {"as_of": results[0]["date"], "bench": bench, "targets": results}
    (out_dir / "snapshot.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [f"# 半导体三条线快照（截至 {payload['as_of']} 收盘）", ""]
    header = (
        "| 标的 | 收盘 | 5日 | 20日 | 60日 | 距60日高 | 距250日高 | 250日分位 | "
        "MA20 | MA60 | 60日波动 | 20日最大回撤 | 5/20日额比 | 20日超额(vs300) | 60日超额 |"
    )
    lines.append(header)
    lines.append("|" + "---|" * 15)

    def fmt(x, pctfmt=True):
        if x is None:
            return "—"
        if pctfmt:
            return f"{x*100:+.1f}%"
        return f"{x:.2f}"

    for st in results:
        lines.append(
            "| {name} | {close:.3f} | {r5} | {r20} | {r60} | {dd60} | {dd250} | {pos} | "
            "{ma20} | {ma60} | {vol} | {mdd} | {amt} | {rs20} | {rs60} |".format(
                name=st["name"],
                close=st["close"],
                r5=fmt(st["r5"]),
                r20=fmt(st["r20"]),
                r60=fmt(st["r60"]),
                dd60=fmt(st["dd60"]),
                dd250=fmt(st["dd250"]),
                pos=fmt(st["pos250"], False),
                ma20=fmt(st["above_ma20"]),
                ma60=fmt(st["above_ma60"]),
                vol=fmt(st["vol60"], False),
                mdd=fmt(st["mdd20"]),
                amt=fmt(st["amt_ratio"], False),
                rs20=fmt(st.get("rs20")),
                rs60=fmt(st.get("rs60")),
            )
        )

    lines.append("")
    lines.append(
        "基准：沪深300 20日 {:.1f}% / 60日 {:.1f}%".format(
            (bench.get("r20") or 0) * 100, (bench.get("r60") or 0) * 100
        )
    )
    (out_dir / "analysis.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("\nfiles:", out_dir / "analysis.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
