# 监视一键日常运行，结束/异常时手机通知
param([int]$AppPid = 0)
$ErrorActionPreference = 'Continue'
$log = 'E:\Code\automation_project\dist\logs\daily_runner.log'
$notify = 'E:\Code\dsh-agent\notify.ps1'
$out = 'E:\Code\quant\work\daily_watch.txt'

"$(Get-Date -Format 'HH:mm:ss') watch start" | Out-File -Append -Encoding UTF8 $out

$result = 'timeout'
for ($i = 0; $i -lt 200; $i++) {
    Start-Sleep -Seconds 30
    $tail = Get-Content $log -Tail 6 -Encoding UTF8 -ErrorAction SilentlyContinue
    $joined = ($tail -join "`n")
    if ($joined -match '\[留档\]') { $result = 'done'; break }
    if ($joined -match '\[中止\]|\[致命\]|Traceback') { $result = 'error'; break }
    if ($AppPid -gt 0) {
        if (-not (Get-Process -Id $AppPid -ErrorAction SilentlyContinue)) { $result = 'exited'; break }
    }
}
$tail = Get-Content $log -Tail 8 -Encoding UTF8 -ErrorAction SilentlyContinue
"$(Get-Date -Format 'HH:mm:ss') watch end: $result" | Out-File -Append -Encoding UTF8 $out
$tail | Out-File -Append -Encoding UTF8 $out

$csv = 'E:\Code\automation_project\dist\logs\daily_results.csv'
$last = Get-Content $csv -Tail 1 -Encoding UTF8 -ErrorAction SilentlyContinue
$text = "一键日常 $result`n最近日志:`n" + (($tail | Select-Object -Last 4) -join "`n") + "`n结果行: $last"
& powershell -NoProfile -ExecutionPolicy Bypass -File $notify -Text $text
"$(Get-Date -Format 'HH:mm:ss') notify sent" | Out-File -Append -Encoding UTF8 $out
