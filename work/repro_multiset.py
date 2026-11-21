"""判断 CSV 差异是「纯行序不同」还是「内容真的变了」。

用法: python work/repro_multiset.py runs/2026-11-14_fc_fastgate
对目录里每个 csv：把 HEAD 版本与工作区版本都按字符串元组做多重集合比较。
"""
import collections
import csv
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def head_text(path):
    out = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=str(ROOT), capture_output=True)
    return None if out.returncode != 0 else out.stdout.decode("utf-8-sig", errors="replace")


def rows(text):
    return list(csv.reader(text.splitlines()))


def main(rel):
    rel = rel.replace("\\", "/")
    target = ROOT / rel
    for p in sorted(target.glob("*.csv")):
        name = p.name
        old = head_text(f"{rel}/{name}")
        if old is None:
            print(f"{name}: untracked")
            continue
        new = p.read_text(encoding="utf-8-sig", errors="replace")
        if old == new:
            continue
        ra = collections.Counter(tuple(r) for r in rows(old))
        rb = collections.Counter(tuple(r) for r in rows(new))
        only_old = sum((ra - rb).values())
        only_new = sum((rb - ra).values())
        if only_old == 0 and only_new == 0:
            print(f"{name}: same rows, different order only")
        else:
            sample = list((ra - rb).items())[:2]
            print(f"{name}: real diff rows_old={only_old} rows_new={only_new} sample={sample}")


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(f"== {a}")
        main(a)
