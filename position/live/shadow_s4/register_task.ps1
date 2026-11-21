# 注册 S4 影子盘的 Windows 计划任务（独立任务 quant_shadow_s4_eod，工作日 15:30）。
# 由用户本人在 Windows 上运行一次（不需要管理员，任务以当前用户身份运行）：
#   powershell -ExecutionPolicy Bypass -File E:\Code\quant\position\live\shadow_s4\register_task.ps1
# 撤销：schtasks /Delete /TN quant_shadow_s4_eod /F
# 不修改 position\live\shadow\daily.ps1 或已有的 v2 任务。
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $Here "run_s4_daily.ps1"
$t = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
schtasks /Create /TN "quant_shadow_s4_eod" /TR $t /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:30 /F
schtasks /Query /TN "quant_shadow_s4_eod" /V /FO LIST | Select-String "TaskName|Next Run Time|Task To Run|下次运行时间|要运行的任务"
