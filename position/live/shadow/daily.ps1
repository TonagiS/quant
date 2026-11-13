param([ValidateSet('1430','eod')][string]$phase = '1430')
$ErrorActionPreference = 'Continue'
Set-Location 'E:\Code\quant'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$py = 'E:\Code\quant\venv\Scripts\python.exe'
$live = 'E:\Code\quant\position\live'
$log = Join-Path $live 'shadow\run.log'
"[{0}] phase={1} start" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $phase | Out-File -FilePath $log -Append -Encoding UTF8
& $py (Join-Path $live 'shadow\run_shadow.py') --phase $phase *>> $log
if ($phase -eq '1430') {
    & $py (Join-Path $live 'paper_account.py') --execute *>> $log
    & $py 'E:\Code\dsh-agent\group_brief.py' *>> $log
}
if ($phase -eq 'eod') {
    & $py (Join-Path $live 'shadow\v2_observ.py') *>> $log
    & $py (Join-Path $live 'paper_account.py') --mark *>> $log
}
"[{0}] phase={1} done exit={2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $phase, $LASTEXITCODE | Out-File -FilePath $log -Append -Encoding UTF8
