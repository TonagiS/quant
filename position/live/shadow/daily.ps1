param([ValidateSet('1430','eod')][string]$phase = '1430')
# 每日流水线：1430 影子→调仓→群播报；eod 观测→计净值
# 2026-11-17 修：① 日志统一 UTF-8（原来 UTF-16 混写，tail 全是乱码）② 群发前等桥就绪 + 失败重试
#                ③ 每步记录退出码，出错发 TG/QQ 通知 ④ 末尾非 0 退出，让计划任务的失败看得见
$ErrorActionPreference = 'Continue'
Set-Location 'E:\Code\quant'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:TQDM_DISABLE = '1'          # 进度条别刷屏
$py = 'E:\Code\quant\venv\Scripts\python.exe'
$live = 'E:\Code\quant\position\live'
$log = Join-Path $live 'shadow\run.log'
$notify = 'E:\Code\dsh-agent\notify_event.ps1'
$fails = New-Object System.Collections.ArrayList

function Write-Log([string]$msg) {
    "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg |
        Out-File -FilePath $log -Append -Encoding UTF8
}

function Send-Alert([string]$text) {
    Write-Log "ALERT $text"
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $notify -Kind error -Text $text | Out-Null
    } catch {
        Write-Log "ALERT 通知发送失败: $($_.Exception.Message)"
    }
}

function Quote-Arg([string]$s) {
    if ($s -match '[\s"]') { return '"' + ($s -replace '"', '\"') + '"' }
    return $s
}

function Invoke-Step([string]$what, [string]$exe, [string[]]$argv, [switch]$Critical) {
    # 走 cmd 重定向直接写日志：PowerShell 的 *>&1 会把 stderr 包成 NativeCommandError 噪音
    $line = '"{0}" {1} >> "{2}" 2>&1' -f $exe, (($argv | ForEach-Object { Quote-Arg $_ }) -join ' '), $log
    Write-Log ("run {0}: {1}" -f $what, $line)
    & cmd /c $line
    $code = $LASTEXITCODE
    if ($null -eq $code) { $code = 0 }
    Write-Log ("step {0} exit={1}" -f $what, $code)
    if ($code -ne 0) {
        [void]$fails.Add("$what exit=$code")
        if ($Critical) { Send-Alert "[$phase] $what 失败（exit=$code），后续步骤已跳过" }
    }
    return $code
}

function Wait-Bridge([int]$maxWaitSec = 180) {
    # 14:32 那次群发失败就是桥没起来（WinError 10061），这里最多等 3 分钟
    $deadline = (Get-Date).AddSeconds($maxWaitSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8721/health' -TimeoutSec 5 -UseBasicParsing
            if ($r.StatusCode -eq 200) { Write-Log 'bridge ready'; return $true }
        } catch { }
        Start-Sleep -Seconds 5
    }
    Write-Log 'bridge 未就绪（等待超时）'
    return $false
}

Write-Log "phase=$phase start"
$abort = $false

if ($phase -eq '1430') {
    $a1430 = @((Join-Path $live 'shadow\run_shadow.py'), '--phase', '1430')
    if ((Invoke-Step 'run_shadow' $py $a1430 -Critical) -ne 0) {
        $abort = $true           # 影子挂了就别拿残数据去调仓
    }
    if (-not $abort) {
        $aExec = @((Join-Path $live 'paper_account.py'), '--execute')
        Invoke-Step 'paper_execute' $py $aExec | Out-Null
        $aExec5 = @((Join-Path $live 'paper_book5d.py'), '--execute')
        Invoke-Step 'paper5d_execute' $py $aExec5 | Out-Null
        if (-not (Wait-Bridge)) { Send-Alert "[$phase] 群播报前桥未就绪，稍后重试" }
        # group_brief 有 sent_YYYYMMDD 标记，重跑安全（不会重复发）
        $aBrief = @('E:\Code\dsh-agent\group_brief.py')
        $ok = $false
        for ($i = 1; $i -le 3; $i++) {
            if ((Invoke-Step "group_brief#$i" $py $aBrief) -eq 0) { $ok = $true; break }
            Start-Sleep -Seconds (30 * $i)
        }
        if (-not $ok) { Send-Alert "[$phase] 群播报 3 次都失败，需人工补发" }
        $aNtDaily = @('E:\Code\quant\tools\nt_daily.py')
        Invoke-Step 'nt_daily' $py $aNtDaily | Out-Null
        # 群号从 dsh-agent/group.json 读（跟 group_brief.py 同一份配置，仓库里不留群号）
        $grp = ''
        try {
            $grp = [string](Get-Content 'E:\Code\dsh-agent\group.json' -Raw -Encoding UTF8 | ConvertFrom-Json).group
        } catch {
            Send-Alert "[$phase] 读 group.json 失败：$($_.Exception.Message)"
        }
        $aNtBrief = @('E:\Code\quant\tools\nt_brief.py', '--short', '--notify')
        if ($grp) { $aNtBrief += @('--group', $grp) }
        Invoke-Step 'nt_brief' $py $aNtBrief | Out-Null
    }
}

if ($phase -eq 'eod') {
    $aObs = @((Join-Path $live 'shadow\v2_observ.py'))
    Invoke-Step 'v2_observ' $py $aObs | Out-Null
    $aMark = @((Join-Path $live 'paper_account.py'), '--mark')
    Invoke-Step 'paper_mark' $py $aMark | Out-Null
    $aMark5 = @((Join-Path $live 'paper_book5d.py'), '--mark')
    Invoke-Step 'paper5d_mark' $py $aMark5 | Out-Null
}

if ((Test-Path $log) -and ((Get-Item $log).Length -gt 4MB)) {
    Move-Item -Path $log -Destination ((Join-Path $live 'shadow') + '\run.log.1') -Force
    Write-Log "日志轮转到 run.log.1（${phase}）"
}
if ($fails.Count -gt 0) {
    Write-Log ("phase={0} done FAILED: {1}" -f $phase, ($fails -join '; '))
    Send-Alert ("[$phase] 流水线有步骤失败：" + ($fails -join '; '))
    exit 1
}
Write-Log ("phase={0} done OK" -f $phase)
exit 0



