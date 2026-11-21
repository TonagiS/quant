# 数据缓存落地 + 复现回测（在 cache_dl2.ps1 之后跑）
#   1) 等 _cache\quant_data_cache_2026-11-14.tar.gz 下完并校验
#   2) 解压到仓库根（还原 data/cache*）
#   3) 备份旧的 summary CSV
#   4) 跑 work\fullmkt_single_asset.py eval（读缓存，不联网）
#   5) 与旧结果比对，写 work\cache_round1.log
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$CacheDir = Join-Path $Root '_cache'
$Tar = Join-Path $CacheDir 'quant_data_cache_2026-11-14.tar.gz'
$Log = Join-Path $PSScriptRoot 'cache_round1.log'
$Py = Join-Path $Root 'venv\Scripts\python.exe'
$RunDir = Join-Path $Root 'runs\2026-11-14_fullmkt_single_asset'
$Bak = Join-Path $PSScriptRoot '_bak_20261114_fullmkt'

function Log($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
  Add-Content -LiteralPath $Log -Value $line -Encoding UTF8
  Write-Output $line
}
Remove-Item $Log -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Bak | Out-Null

# 1) 等下载完成
Log 'waiting for tarball...'
$deadline = (Get-Date).AddMinutes(40)
while ((Get-Date) -lt $deadline) {
  if ((Test-Path $Tar) -and (Select-String -Path (Join-Path $PSScriptRoot 'cache_dl2.log') -Pattern 'CACHE_DL_DONE' -Quiet)) { break }
  Start-Sleep -Seconds 15
}
if (-not (Select-String -Path (Join-Path $PSScriptRoot 'cache_dl2.log') -Pattern 'CACHE_DL_DONE' -Quiet)) { Log 'download not done, abort'; exit 1 }
Log ("tarball ok {0:N2} MB" -f ((Get-Item $Tar).Length/1MB))

# 2) 解压
Log 'extracting...'
& tar.exe -xzf $Tar -C $Root
if ($LASTEXITCODE -ne 0) { Log "tar failed $LASTEXITCODE"; exit 1 }
$dirs = Get-ChildItem (Join-Path $Root 'data') -Directory | Where-Object Name -like 'cache_*' | Select-Object -ExpandProperty Name
Log ("data cache dirs: " + ($dirs -join ', '))

# 3) 备份旧结果
foreach ($f in @('summary_by_group.csv', 'focus_results.csv', 'results_long.csv', 'REPORT.md')) {
  $src = Join-Path $RunDir $f
  if (Test-Path $src) { Copy-Item $src (Join-Path $Bak $f) -Force }
}
Log 'old results backed up'

# 4) 跑 eval
Log 'running fullmkt eval...'
$sw = [Diagnostics.Stopwatch]::StartNew()
& $Py (Join-Path $Root 'work\fullmkt_single_asset.py') eval *> (Join-Path $PSScriptRoot 'fullmkt_eval_rerun.log')
$code = $LASTEXITCODE
Log ("eval exit=$code in {0:N1} min" -f $sw.Elapsed.TotalMinutes)
if ($code -ne 0) { Log 'eval failed'; exit 1 }

# 5) 比对
$py = @'
import csv, sys, pathlib
run = pathlib.Path(r"%RUN%"); bak = pathlib.Path(r"%BAK%")
def load(p):
    with open(p, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))
for name in ["summary_by_group.csv", "focus_results.csv"]:
    a, b = bak / name, run / name
    if not (a.exists() and b.exists()):
        print(name, "MISSING", a.exists(), b.exists()); continue
    ra, rb = load(a), load(b)
    print(name, "rows", len(ra), "->", len(rb))
    if len(ra) != len(rb):
        print("  ROW COUNT DIFF"); continue
    diffs = 0
    keys = ra[0].keys()
    for x, y in zip(ra, rb):
        for k in keys:
            if x[k] != y.get(k):
                diffs += 1
                if diffs <= 5:
                    print("  diff", x.get(keys[0]), k, x[k], "->", y.get(k))
    print("  cell diffs:", diffs)
'@
$py = $py.Replace('%RUN%', $RunDir).Replace('%BAK%', $Bak)
$tmp = Join-Path $PSScriptRoot 'diff_old_new.py'
Set-Content -LiteralPath $tmp -Value $py -Encoding UTF8
$out = & $Py $tmp 2>&1
$out | ForEach-Object { Log $_ }
Log 'CACHE_ROUND1_DONE'
