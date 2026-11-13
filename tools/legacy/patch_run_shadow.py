"""给 run_shadow.px_close 加收盘价兜底（腾讯日线 -> 新浪 15:00 bar）。"""
import ast
import os

P = r"E:\Code\quant\shadow\run_shadow.py"
s = open(P, encoding="utf-8").read()

HELPER = '''def _sina_close(code):
    """腾讯日线在收盘后一段时间内可能还没有当日 bar，用新浪 30 分钟线的 15:00 收盘兜底。"""
    import akshare as ak
    df = ak.stock_zh_a_minute(symbol=code, period="30", adjust="")
    df["day"] = pd.to_datetime(df["day"])
    t = df[df["day"].dt.date == date.today()]
    if t.empty:
        raise ValueError("no today bar")
    return float(pd.to_numeric(t.iloc[-1]["close"], errors="coerce"))


def px_close(code):'''

assert "def _sina_close" not in s, "已打过补丁"
assert "def px_close(code):" in s
s = s.replace("def px_close(code):", HELPER, 1)

OLD = '''    df = df[df["date"].dt.date == date.today()]
    if df.empty:
        raise ValueError("no daily bar today")'''
NEW = '''    df = df[df["date"].dt.date == date.today()]
    if df.empty:
        return _sina_close(code)   # 腾讯日线更新慢，兜底新浪 15:00 bar'''
assert OLD in s
s = s.replace(OLD, NEW, 1)

open(P, "w", encoding="utf-8").write(s)
ast.parse(s)
print("patched", P, os.path.getsize(P), "bytes")
