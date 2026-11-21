# 收盘后刷行情缓存（计划任务 quant_cache_refresh，交易日 18:20）
# 2026-11-17 建：cache_ohlc / cache_ohlc_full 之前没人刷，停在 11-13，单支规则用旧价算 T 档
$ErrorActionPreference = 'Continue'
Set-Location 'E:\Code\quant'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:TQDM_DISABLE = '1'
$py = 'E:\Code\quant\venv\Scripts\python.exe'
$log = 'E:\Code\quant\position\live\shadow\run.log'
$notify = 'E:\Code\dsh-agent\notify_event.ps1'

function Write-Log([string]$msg) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg |
        Out-File -FilePath $log -Append -Encoding UTF8
}

Write-Log 'cache_refresh start'
& cmd /c ('"{0}" "{1}" >> "{2}" 2>&1' -f $py, 'E:\Code\quant\tools\cache_refresh.py', $log)
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
Write-Log "cache_refresh done exit=$code"
if ($code -ne 0) {
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $notify -Kind error -Text "行情缓存刷新失败 exit=$code（见 run.log）" | Out-Null
    } catch { }
}
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 4MB)) {
    Move-Item -Path $log -Destination 'E:\Code\quant\position\live\shadow\run.log.1' -Force
    Write-Log '日志轮转到 run.log.1（cache_refresh）'
}
exit $code


