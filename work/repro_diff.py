"""对比工作区里的 runs 产物与 HEAD 提交版本，报告数值差异幅度。

用法: python work/repro_diff.py runs/2026-11-14_layered_v1
"""
import csv
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def head_text(path):
    out = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=str(ROOT), capture_output=True,
    )
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8-sig", errors="replace")


def parse(text):
    return list(csv.DictReader(text.splitlines()))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main(rel):
    rel = rel.replace("\\", "/")
    target = ROOT / rel
    if not target.is_dir():
        print(f"{rel}: no such dir")
        return
    files = sorted(p.name for p in target.glob("*.csv"))
    for name in files:
        p = f"{rel}/{name}"
        old = head_text(p)
        new = (target / name).read_text(encoding="utf-8-sig", errors="replace")
        if old is None:
            print(f"{name}: NEW (untracked in HEAD)")
            continue
        if old == new:
            print(f"{name}: identical")
            continue
        ra, rb = parse(old), parse(new)
        if len(ra) != len(rb):
            print(f"{name}: ROW COUNT {len(ra)} -> {len(rb)}")
            continue
        keys = list(ra[0].keys()) if ra else []
        cells = 0
        maxabs = 0.0
        maxinfo = ""
        for x, y in zip(ra, rb):
            for k in keys:
                a, b = x.get(k, ""), y.get(k, "")
                if a == b:
                    continue
                cells += 1
                fa, fb = num(a), num(b)
                if fa is not None and fb is not None:
                    d = abs(fa - fb)
                    if d > maxabs:
                        maxabs = d
                        maxinfo = f"{x.get(keys[0])}.{k} {a}->{b}"
                elif cells <= 3:
                    maxinfo = maxinfo or f"{x.get(keys[0])}.{k} {a!r}->{b!r}"
        print(f"{name}: rows {len(ra)}, cells changed {cells}, max |Δ| {maxabs:.3g} [{maxinfo}]")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(f"== {arg}")
        main(arg)
