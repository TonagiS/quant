"""用 Firefox cookie + wbi 拉指定 UP 的最近投稿（防 412/风控）。"""
import hashlib
import json
import sys
import time
import urllib.parse

import requests

sys.path.insert(0, r"E:\AI\DSH-Works\agent-workspace\bili")
from bili_video_info import load_cookies, UA  # noqa: E402

MID = int(sys.argv[1]) if len(sys.argv) > 1 else 32222710

S = requests.Session()
S.headers.update({"User-Agent": UA, "Referer": "https://space.bilibili.com/%d/video" % MID,
                  "Origin": "https://space.bilibili.com", "Accept": "application/json, text/plain, */*"})
cookies = load_cookies()
print("cookies:", "SESSDATA ok" if cookies else "none")
for k, v in cookies.items():
    S.cookies.set(k, v, domain=".bilibili.com")

MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def wbi_key():
    d = S.get("https://api.bilibili.com/x/web-interface/nav", timeout=20).json()
    wi = d["data"]["wbi_img"]
    img = wi["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub = wi["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    raw = img + sub
    return "".join(raw[i] for i in MIXIN_TAB)[:32]


def sign(params, mixin):
    params = dict(params)
    params["wts"] = int(time.time())
    q = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    params["w_rid"] = hashlib.md5((q + mixin).encode()).hexdigest()
    return params


mixin = wbi_key()
p = sign({"mid": MID, "ps": 20, "pn": 1, "order": "pubdate", "platform": "web",
          "web_location": 1550101, "order_avoided": "true", "dm_img_list": "[]",
          "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
          "dm_cover_img_str": "QU5HTEUgKEludGVsLCBJbnRlbChSKSBVSEQgR3JhcGhpY3MgNjMwKQ",
          "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}'}, mixin)
r = S.get("https://api.bilibili.com/x/space/wbi/arc/search", params=p, timeout=25)
try:
    d = r.json()
except Exception:
    print("NON-JSON", r.status_code, r.text[:300])
    raise SystemExit(1)
print("code:", d.get("code"), d.get("message"))
if d.get("code") == 0:
    vl = d["data"]["list"]["vlist"]
    print("total:", d["data"]["page"]["count"])
    for v in vl[:12]:
        print(time.strftime("%Y-%m-%d %H:%M", time.localtime(v["created"])), v["bvid"],
              v["length"], "|", v["title"])
    json.dump(vl, open(r"E:\Code\quant\work\bili_up_latest.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
