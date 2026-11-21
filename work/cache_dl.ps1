# 多连接并行下载数据缓存 release（单连接被限速到 ~30-50 KB/s，6 连接能到 ~280 KB/s）
# 用法: powershell -NoProfile -File work\cache_dl.ps1
# 产物: _cache\quant_data_cache_2026-11-14.tar.gz + 校验
$ErrorActionPreference = 'Stop'
$Repo = 'TonagiS/quant'
$Tag = 'data-cache-2026-11-14'
$Asset = 'quant_data_cache_2026-11-14.tar.gz'
$Root = Split-Path -Parent $PSScriptRoot          # 仓库根目录（work 的上一级）
$CacheDir = Join-Path $Root '_cache'
$Parts = Join-Path $CacheDir 'parts'
$Out = Join-Path $CacheDir $Asset
$Log = Join-Path $PSScriptRoot 'cache_dl.log'
$Url = "https://github.com/$Repo/releases/download/$Tag/$Asset"
# 实测：单连接 ~30-50 KB/s，6 连接 ~260 KB/s；12 连接会被 GitHub 卡成 0 字节，所以固定 6
$N = 6

function Log($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
  Add-Content -LiteralPath $Log -Value $line -Encoding UTF8
  Write-Output $line
}

New-Item -ItemType Directory -Force -Path $Parts | Out-Null
Remove-Item $Log -ErrorAction SilentlyContinue
Log "start $Asset"

# 1) 总大小（先 curl HEAD 拿 Content-Length，避开 API 限频）
$Size = 0
$hdrs = & curl.exe -sIL --retry 3 $Url 2>&1
foreach ($h in $hdrs) {
  if ($h -match '^content-length:\s*(\d+)') { $Size = [int64]$Matches[1] }
}
if ($Size -le 0) {
  try {
    $rel = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/tags/$Tag" -Headers @{ 'User-Agent'='dsh' } -TimeoutSec 30
    $asset = @($rel.assets) | Where-Object { $_.name -eq $Asset } | Select-Object -First 1
    if ($asset) { $Size = [int64]$asset.size }
  } catch { Log "api size failed: $($_.Exception.Message)" }
}
$asset = $null
if ($Size -le 0) { Log 'cannot determine size'; exit 1 }
Log ("size {0:N2} MB, split into {1} parts" -f ($Size/1MB), $N)

# 2) 分片并行下载（分 4 轮补漏：哪个片长度不对就重下那一片）
$chunk = [int64][math]::Ceiling($Size / $N)
$ranges = @()
for ($i = 0; $i -lt $N; $i++) {
  $from = $i * $chunk
  $to = [math]::Min($Size - 1, ($i + 1) * $chunk - 1)
  if ($from -gt $to) { continue }
  $ranges += [pscustomobject]@{
    Path = (Join-Path $Parts ("p{0:D2}.bin" -f $i))
    From = $from; To = $to; Want = $to - $from + 1
  }
}

$sw = [Diagnostics.Stopwatch]::StartNew()
for ($round = 1; $round -le 4; $round++) {
  $todo = @($ranges | Where-Object { -not ((Test-Path $_.Path) -and ((Get-Item $_.Path).Length -eq $_.Want)) })
  if ($todo.Count -eq 0) { break }
  foreach ($r in $todo) {
    if (Test-Path $r.Path) { Remove-Item $r.Path -Force }
  }
  $procs = @()
  foreach ($r in $todo) {
    $procs += Start-Process curl.exe -ArgumentList @(
      '-sL', '--retry', '5', '--retry-delay', '3',
      '-r', "$($r.From)-$($r.To)", '-o', $r.Path, $Url
    ) -WindowStyle Hidden -PassThru
  }
  Log "round $round launched $($procs.Count) parts"

  while ($true) {
    Start-Sleep -Seconds 20
    $done = (Get-ChildItem $Parts -Filter 'p*.bin' | Measure-Object Length -Sum).Sum
    Log ("progress {0:N1}/{1:N1} MB ({2:N0}%) {3:N0} KB/s" -f ($done/1MB), ($Size/1MB), (100*$done/$Size), ($done/1KB/$sw.Elapsed.TotalSeconds))
    $alive = @($procs | Where-Object { -not $_.HasExited })
    if ($alive.Count -eq 0) { break }
    if ($sw.Elapsed.TotalMinutes -gt 100) { Log 'TIMEOUT'; exit 1 }
  }
  # 卡成 0 字节的连接会一直挂着：这一轮等完后强制清场，下一轮重下
  Get-Process curl -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

$got = (Get-ChildItem $Parts -Filter 'p*.bin' | Measure-Object Length -Sum).Sum
if ($got -ne $Size) { Log ("SIZE MISMATCH got {0} want {1}" -f $got, $Size); exit 1 }

# 3) 合并
if (Test-Path $Out) { Remove-Item $Out -Force }
$fs = [System.IO.File]::Create($Out)
try {
  Get-ChildItem $Parts -Filter 'p*.bin' | Sort-Object Name | ForEach-Object {
    $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
    $fs.Write($bytes, 0, $bytes.Length)
  }
} finally { $fs.Close() }
Log ("merged {0:N2} MB" -f ((Get-Item $Out).Length/1MB))

# 4) 校验
$sumUrl = "https://github.com/$Repo/releases/download/$Tag/SHA256SUMS.txt"
$sumFile = Join-Path $CacheDir 'SHA256SUMS.txt'
& curl.exe -sL --retry 3 -o $sumFile $sumUrl
$expect = (Get-Content $sumFile | Where-Object { $_ -match [regex]::Escape($Asset) }) -split '\s+' | Select-Object -First 1
$actual = (Get-FileHash -LiteralPath $Out -Algorithm SHA256).Hash.ToLower()
Log "sha256 expect=$expect"
Log "sha256 actual=$actual"
if ($expect -and ($expect.ToLower() -ne $actual)) { Log 'CHECKSUM MISMATCH'; exit 1 }
Remove-Item $Parts -Recurse -Force
Log 'CACHE_DL_DONE'
