# -*- coding: utf-8 -*-
"""测试纸面账本的敞口回收规则（不碰真账本：拷到 work/_paper_test 再跑）。

场景：把 book_targets 打桩成"目标敞口 60%"，账本自然停在 ~100%，
验证 vt15 触发回收、s2 不触发。
"""
import json
import os
import shutil
import sys

WORK = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(WORK), "position", "live"))

import paper_account as pa  # noqa: E402

TEST = os.path.join(WORK, "_paper_test")
REAL = os.path.join(pa.BOOKS_DIR)
FAKE_EXPO = 0.60


def fake_targets(name, vt, use_adx=False):
    st = pa.load(os.path.join(pa.SHADOW, "state.json"), {"last_targets": {}})
    t = st.get("last_targets", {}) or {}
    expo = FAKE_EXPO if vt else 1.0
    return {c: w * expo for c, w in t.items()}, expo, {"note": "test 桩"}, "TEST-2026-11-17"


def prep(book):
    dst = os.path.join(TEST, book)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(os.path.join(REAL, book), dst)
    p = os.path.join(dst, "account.json")
    with open(p, encoding="utf-8") as fp:
        acc = json.load(fp)
    acc["last_rebalance"] = ""
    with open(p, "w", encoding="utf-8") as fp:
        json.dump(acc, fp, indent=2, ensure_ascii=False)


def snapshot(book):
    with open(os.path.join(TEST, book, "account.json"), encoding="utf-8") as fp:
        acc = json.load(fp)
    px = acc.get("last_px") or {}
    pos = sum(v["shares"] * px.get(c, v["cost"]) for c, v in acc["positions"].items())
    nav = acc["cash"] + pos
    return nav, pos / nav, {c: v["shares"] for c, v in acc["positions"].items()}


def main():
    os.makedirs(TEST, exist_ok=True)
    pa.BOOKS_DIR = TEST
    pa.book_targets = fake_targets
    for book, label, vt in (("vt15", "VT15", 0.15), ("s2", "S2满仓", None)):
        print("=== %s（目标敞口 %.0f%%，vt=%s）===" % (book, FAKE_EXPO * 100 if vt else 100, vt))
        prep(book)
        nav0, e0, sh0 = snapshot(book)
        print("  调仓前：净值 %.0f，敞口 %.1f%%" % (nav0, e0 * 100))
        pa.trade_book(book, label, vt)
        nav1, e1, sh1 = snapshot(book)
        print("  调仓后：净值 %.0f，敞口 %.1f%%  持仓变化：" % (nav1, e1 * 100))
        for c in sorted(set(sh0) | set(sh1)):
            if sh0.get(c) != sh1.get(c):
                print("    %s %d -> %d" % (c, sh0.get(c, 0), sh1.get(c, 0)))


if __name__ == "__main__":
    main()
