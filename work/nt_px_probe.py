import urllib.request

codes = ["510050", "510300", "510310", "510330", "510500", "512500", "512100", "588000", "588080"]
url = "https://hq.sinajs.cn/list=" + ",".join("sh" + c for c in codes)
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
body = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "replace")
for line in body.splitlines():
    if "=" not in line:
        continue
    head, _, rest = line.partition("=")
    code = head.split("_")[-1]
    f = rest.strip().strip(";").strip('"').split(",")
    print(code, f[0], f[3])
