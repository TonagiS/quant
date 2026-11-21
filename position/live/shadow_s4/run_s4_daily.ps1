# S4 影子盘每日入口（Windows）。由 register_task.ps1 注册的计划任务调用；也可手动运行：
#   powershell -ExecutionPolicy Bypass -File E:\Code\quant\position\live\shadow_s4\run_s4_daily.ps1 [-DryRun]
# 与 position\live\shadow\daily.ps1 完全独立，不读写 v2 影子盘任何文件。
param([switch]$DryRun)
$ErrorActionPreference = "Continue"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $Here "..\..\..")).Path
$Py = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
$Log = Join-Path $Here "run.log"
Set-Location $Root
$env:PYTHONIOENCODING = "utf-8"
$argsList = @("position\live\shadow_s4\run_s4_shadow.py")
if ($DryRun) { $argsList += "--dry-run" }
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') run_s4_shadow $($argsList -join ' ')" | Out-File -FilePath $Log -Append -Encoding utf8
& $Py @argsList *>> $Log
"exit=$LASTEXITCODE" | Out-File -FilePath $Log -Append -Encoding utf8
