# rerun all 2026-11-14 backtests against the extracted release cache and diff vs HEAD
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root 'venv\Scripts\python.exe'
$Log = Join-Path $PSScriptRoot 'repro_all.log'
Remove-Item $Log -ErrorAction SilentlyContinue

function Log($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
  Add-Content -LiteralPath $Log -Value $line -Encoding UTF8
  Write-Output $line
}

$jobs = @(
  @{ Script = 'statevol_v4_backtest.py';   Run = 'runs/2026-11-14_statevol_v4';        Arg = '' },
  @{ Script = 'bull_switch_backtest.py';   Run = 'runs/2026-11-14_bull_switch';        Arg = '' },
  @{ Script = 'bull_fast_backtest.py';     Run = 'runs/2026-11-14_bull_fast';          Arg = '' },
  @{ Script = 'fastcap_v5_backtest.py';    Run = 'runs/2026-11-14_fastcap_v5';         Arg = '' },
  @{ Script = 'fc_fastgate_backtest.py';   Run = 'runs/2026-11-14_fc_fastgate';        Arg = '' },
  @{ Script = 'fastcap_ma_sens.py';        Run = 'runs/2026-11-14_fastcap_ma_sens';    Arg = '' },
  @{ Script = 'candidates_v2_backtest.py'; Run = 'runs/2026-11-14_candidates_v2';      Arg = '' },
  @{ Script = 'chinext_ai_backtest.py';    Run = 'runs/2026-11-14_chinext_ai';         Arg = '' },
  @{ Script = 'highvol_stocks_backtest.py';Run = 'runs/2026-11-14_highvol_stocks';     Arg = '' },
  @{ Script = 'ablation_v3_backtest.py';   Run = 'runs/2026-11-14_ablation_v3';        Arg = '' },
  @{ Script = 'statevol_v4_episodes.py';   Run = 'runs/2026-11-14_statevol_v4';        Arg = '' },
  @{ Script = 'layered_v1_backtest.py';    Run = 'runs/2026-11-14_layered_v1';         Arg = '' },
  @{ Script = 'long_history_backtest.py';  Run = 'runs/2026-11-14_long_history';       Arg = '' },
  @{ Script = 'fullmkt_single_asset.py';   Run = 'runs/2026-11-14_fullmkt_single_asset'; Arg = 'eval' }
)

Push-Location $Root
git checkout -- runs/ 2>$null
Log 'start'

foreach ($j in $jobs) {
  $name = $j.Script
  $expected = Join-Path $Root ($j.Run -replace '/', '\')
  $before = (Get-ChildItem $expected -ErrorAction SilentlyContinue | Measure-Object).Count
  Log "--- $name (expect $($j.Run), files=$before)"
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $argv = @()
  if ($j.Arg) { $argv += $j.Arg }
  $argv += (Join-Path $Root "work\$name")
  & $Py @argv *> (Join-Path $PSScriptRoot ("repro_{0}.log" -f ($name -replace '\.py$', '')))
  $code = $LASTEXITCODE
  Log ("    exit={0} in {1:N1}s" -f $code, $sw.Elapsed.TotalSeconds)
  if ($code -ne 0) {
    Log "    tail:"
    Get-Content (Join-Path $PSScriptRoot ("repro_{0}.log" -f ($name -replace '\.py$', ''))) -Tail 6 | ForEach-Object { Log "      $_" }
  }
  $diff = & $Py (Join-Path $PSScriptRoot 'repro_diff.py') $j.Run 2>&1
  $diff | ForEach-Object { Log "    $_" }
  git checkout -- runs/ 2>$null
}
Pop-Location
Log 'REPRO_ALL_DONE'
