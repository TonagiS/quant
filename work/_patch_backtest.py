from pathlib import Path

p = Path(r"E:\Code\quant\tools\nt_rule_backtest.py")
s = p.read_text(encoding="utf-8")
pairs = [
    ('def load_panel() -> pd.DataFrame:\n    df = pd.read_csv(SHARES, parse_dates=["date"], dtype={"code": str})',
     'def load_panel(path: Path | str = SHARES) -> pd.DataFrame:\n    df = pd.read_csv(path, parse_dates=["date"], dtype={"code": str})'),
    ('def build_state(cum: pd.Series, m: float) -> np.ndarray:',
     'def build_state(cum: pd.Series, m: float, l: int = L, k: int = K) -> np.ndarray:'),
    ('        lo = i + L\n        hi = min(n, lo + K)',
     '        lo = i + l\n        hi = min(n, lo + k)'),
    ('    ap.add_argument("--m", default="-3,-5,-8,-10,-12,-15")',
     '    ap.add_argument("--m", default="-3,-5,-8,-10,-12,-15")\n    ap.add_argument("--shares", default=str(SHARES))'),
    ('    panel = load_panel()', '    panel = load_panel(Path(a.shares))'),
]
for old, new in pairs:
    assert old in s, old[:60]
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("patched ok")
