# 小块分片 + 6 并发下载 release 缓存。
# 背景：单连接 ~43 KB/s；大于 ~8 MB 的区间请求会被卡成 0 字节，2 MB 小块稳定。
# 用法: powershell -NoProfile -File work\cache_dl2.ps1
$ErrorActionPreference = 'Stop'
$Repo = 'TonagiS/quant'
$Tag = 'data-cache-2026-11-14'
$Asset = 'quant_data_cache_2026-11-14.tar.gz'
$Root = Split-Path -Parent $PSScriptRoot
$CacheDir = Join-Path $Root '_cache'
$ChunkDir = Join-Path $CacheDir 'chunks'
$Out = Join-Path $CacheDir $Asset
$Log = Join-Path $PSScriptRoot 'cache_dl2.log'
$Base = "https://github.com/$Repo/releases/download/$Tag/$Asset"
$Urls = @("https://gh-proxy.com/$Base", "https://ghfast.top/$Base", $Base)
$ChunkSize = 2MB
$Workers = 6

function Log($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
  Add-Content -LiteralPath $Log -Value $line -Encoding UTF8
  Write-Output $line
}

New-Item -ItemType Directory -Force -Path $ChunkDir | Out-Null
Remove-Item $Log -ErrorAction SilentlyContinue
Log "start $Asset"

$hdrs = & curl.exe -sIL --retry 3 $Urls[0] 2>&1
$Size = 0
foreach ($h in $hdrs) { if ($h -match '^content-length:\s*(\d+)') { $Size = [int64]$Matches[1] } }
if ($Size -le 0) { Log 'cannot determine size'; exit 1 }
$nChunks = [int][math]::Ceiling($Size / $ChunkSize)
Log ("size {0:N2} MB -> {1} chunks x {2} MB, {3} workers" -f ($Size/1MB), $nChunks, ($ChunkSize/1MB), $Workers)

function ChunkPath($i) { Join-Path $ChunkDir ("c{0:D4}.bin" -f $i) }
function ChunkWant($i) {
  $from = [int64]$i * $ChunkSize
  $to = [math]::Min($Size - 1, $from + $ChunkSize - 1)
  $len = $to - $from + 1
  return [pscustomobject]@{ From = $from; To = $to; Len = $len }
}

$sw = [Diagnostics.Stopwatch]::StartNew()
$queue = New-Object System.Collections.Queue
for ($i = 0; $i -lt $nChunks; $i++) {
  $w = ChunkWant $i
  $p = ChunkPath $i
  if ((Test-Path $p) -and ((Get-Item $p).Length -eq $w.Len)) { continue }
  if (Test-Path $p) { Remove-Item $p -Force }
  $queue.Enqueue($i)
}
Log "queue $($queue.Count) chunks"
$active = @{}
$lastLog = Get-Date
while ($queue.Count -gt 0 -or $active.Count -gt 0) {
  while ($active.Count -lt $Workers -and $queue.Count -gt 0) {
    $i = $queue.Dequeue()
    $w = ChunkWant $i
    $proc = Start-Process curl.exe -ArgumentList @(
      '-sL', '--max-time', '90', '-r', "$($w.From)-$($w.To)", '-o', (ChunkPath $i), $Urls[$i % $Urls.Count]
    ) -WindowStyle Hidden -PassThru
    $active[$proc.Id] = @{ Proc = $proc; Index = $i }
  }
  Start-Sleep -Milliseconds 1500
  foreach ($k in @($active.Keys)) {
    $entry = $active[$k]
    if ($entry.Proc.HasExited) {
      $w = ChunkWant $entry.Index
      $p = ChunkPath $entry.Index
      $ok = (Test-Path $p) -and ((Get-Item $p).Length -eq $w.Len)
      if (-not $ok) {
        if (Test-Path $p) { Remove-Item $p -Force }
        if (-not $entry.Retried) {
          $entry.Retried = $true
          $queue.Enqueue($entry.Index)     # 重下一次
        } else {
          Log "chunk $($entry.Index) failed twice"
        }
      }
      $active.Remove($k)
    }
  }
  if (((Get-Date) - $lastLog).TotalSeconds -ge 30) {
    $lastLog = Get-Date
    $done = (Get-ChildItem $ChunkDir -Filter 'c*.bin' | Measure-Object Length -Sum).Sum
    Log ("progress {0:N1}/{1:N1} MB ({2:N0}%) {3:N0} KB/s left={4}" -f ($done/1MB), ($Size/1MB), (100*$done/$Size), ($done/1KB/$sw.Elapsed.TotalSeconds), $queue.Count)
  }
  if ($sw.Elapsed.TotalMinutes -gt 120) { Log 'TIMEOUT'; exit 1 }
}

$got = (Get-ChildItem $ChunkDir -Filter 'c*.bin' | Measure-Object Length -Sum).Sum
if ($got -ne $Size) { Log ("SIZE MISMATCH got {0} want {1}" -f $got, $Size); exit 1 }

if (Test-Path $Out) { Remove-Item $Out -Force }
$fs = [System.IO.File]::Create($Out)
try {
  for ($i = 0; $i -lt $nChunks; $i++) {
    $p = ChunkPath $i
    $buf = [System.IO.File]::ReadAllBytes($p)
    $fs.Write($buf, 0, $buf.Length)
  }
} finally { $fs.Close() }
Log ("merged {0:N2} MB" -f ((Get-Item $Out).Length/1MB))

$sumFile = Join-Path $CacheDir 'SHA256SUMS.txt'
& curl.exe -sL --retry 3 --max-time 60 -o $sumFile "https://gh-proxy.com/https://github.com/$Repo/releases/download/$Tag/SHA256SUMS.txt"
$expect = ((Get-Content $sumFile | Where-Object { $_ -match [regex]::Escape($Asset) }) -split '\s+')[0]
$actual = (Get-FileHash -LiteralPath $Out -Algorithm SHA256).Hash.ToLower()
Log "sha256 expect=$expect"
Log "sha256 actual=$actual"
if ($expect -and ($expect.ToLower() -ne $actual)) { Log 'CHECKSUM MISMATCH'; exit 1 }
Remove-Item $ChunkDir -Recurse -Force
Log 'CACHE_DL_DONE'




