# 一键重跑仓位研究（POS-01 + POS-02 + POS-09），不需要交互/授权
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File position\run_all.ps1
$ErrorActionPreference = 'Stop'
Set-Location 'E:\Code\quant'
$py = 'E:\Code\quant\venv\Scripts\python.exe'
$log = Join-Path 'E:\Code\quant\work' ("pos_all_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
"[{0}] start" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Out-File -FilePath $log -Encoding UTF8
& $py 'position\run_grid.py' *>> $log
& $py 'position\advanced.py' *>> $log
& $py 'position\buffer_vt.py' *>> $log
"[{0}] done exit={1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $LASTEXITCODE | Out-File -FilePath $log -Append -Encoding UTF8
Write-Host "done: $log"
