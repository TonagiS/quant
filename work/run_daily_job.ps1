$ErrorActionPreference = 'Continue'
$prog = 'E:\Code\quant\work\job_progress.txt'
$log = 'E:\Code\automation_project\dist\logs\daily_runner.log'
function Log($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -Append -Encoding utf8 $prog }

Remove-Item 'E:\Code\quant\work\autorun_err.txt' -ErrorAction SilentlyContinue
Remove-Item $prog -ErrorAction SilentlyContinue
Log "job start"
if (Test-Path $log) { $startLen = (Get-Item $log).Length } else { $startLen = 0 }
Log "log start len=$startLen"

$py = 'E:\Code\automation_project\venv\Scripts\python.exe'
$p = Start-Process -FilePath $py -ArgumentList 'E:\Code\quant\work\autorun_daily.py' -Verb RunAs -PassThru
Log "launched pid=$($p.Id)"

for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 20
    if (Test-Path 'E:\Code\quant\work\autorun_err.txt') {
        Log "ERROR file present"
        break
    }
    if (Test-Path $log) {
        $len = (Get-Item $log).Length
        if ($len -gt $startLen) {
            $tail = Get-Content $log -Tail 3 -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($tail -match '\[留档\]|\[结束\]|全部.*完成') {
                Log "finished marker found"
                break
            }
            Log "running len=$len"
        } else {
            Log "waiting for log growth"
        }
    }
}
Log "job done"
