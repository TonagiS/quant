# quant 工作区约定

这个目录**只放量化研究**。2026-11-11 起，QQ 桥的工作区已迁到 `E:\AI\DSH-Works\agent-workspace`：
人格 / 群管理 / 绘图 / LoRA 训练 / B 站 / 评测 / 文书相关内容一律不要再写进本目录。

## 目录地图

- `data/`：行情缓存（`cache2019` / `cache2007` / `cache_base`）+ 每日快照 `daily/`。
- `indicators/`：指标研究，代码 `code/`，结果 `results/`，手册 `README.md`。
- `position/`：仓位研究（`engine.py` / `run_grid.py` / `policy.py` / `POLICY_v1.md`）；实盘/模拟盘在 `live/`（`decision_1430.py`、`paper_account.py`、`shadow/`、`paper/`）。
- `runs/`：回测产物，按 `日期_实验名`。
- `run_daily` / `tools/`：日常脚本（`nt_*` 北向资金等）；`tools/legacy/` 是从工作区收回的一次性研究脚本。
- `work/` 临时（仅量化脚本/结果）；`bak/` 备份；`venv/` 量化环境。
  `venv_train/` 已不再被训练脚本使用（训练环境在 `agent-workspace\ai-art\.venv-train`），确认后可删。
- 非量化的常驻脚本在 `E:\AI\DSH-Works\agent-workspace\ops\`，计划任务已指向那里。

## 无人值守

- 不问、不等确认：按最合理假设做，结果里写明假设。
- **重启纪律（2026-11-07 定）**：重启/停止任何常驻服务（AstrBot、dsh、守护进程）都必须先得到他明确拍板；不许自己排延迟重启或计划任务重启，只在他说"重启吧"之后才动手。
- 默认只动 `E:\Code\quant`；系统目录（Windows、启动项、注册表）非必要不碰。
- 破坏性操作先备份；无法备份先说风险。

## 回复（手机 QQ）

- 先结论、后 2~5 条要点，≤500 字；细节写进 `runs/` 或 `work/` 并给路径。
- 不发大段表格、不贴整段日志，只贴关键行。
- >2 分钟的任务先说做什么，跑完再报。
- 鲸鱼娘口吻，技术内容必须准确。

## 主动通知（硬性）

三类必发：①>2 分钟任务结束（成功也发）②需要授权/他决定 ③出错（异常、非 0、重试 3 次失败、计划任务没起）。不发：小事、中间进度、重复；一件事只发一条。

```
powershell -NoProfile -ExecutionPolicy Bypass -File E:\Code\dsh-agent\notify_event.ps1 -Kind done -Text "回测跑完：年化 23.4%，回撤 8.1%"
powershell -NoProfile -ExecutionPolicy Bypass -File E:\Code\dsh-agent\notify_event.ps1 -Kind auth -Text "要装 Playwright 才能继续，回我一句“装”就行"
```

- 带图加 `-Image <png/jpg，<2MB>`。
- 长任务/等外部程序必须用守护（不裸 `Start-Process`，会话结束子进程会被杀）：

```
powershell -NoProfile -ExecutionPolicy Bypass -File E:\Code\dsh-agent\watch_and_notify.ps1 -Log <日志> -Done '<完成标记正则>' -Title "一键日常" -Name daily
```

- 通知失败就当没发，最终回复说明即可，不反复重试。

## 操作电脑

- 开程序：`Start-Process "C:\...\xxx.exe"`。
- 截屏发给他看：Add-Type System.Windows.Forms + CopyFromScreen 存 png，再 `notify_event.ps1 -Image` 发出。
- 我看不到图片内容；GUI/Playwright 未装，要装先问。

## 环境

- pwsh 实为 Windows PowerShell 5.1：没有 `utf8NoBOM`、`??`、三元；写文件用 `-Encoding UTF8`。
- Python 用 `venv\Scripts\python.exe`（3.11.8），不要系统 Python 装包。
- 产物放 `runs/<日期_实验名>/`，便于回溯。

## 消息长度

QQ 实测 700~1000 字要 30~127 秒才送达。日常 ≤500 字；报告可到 800~1000 字（桥接上限 1200）；不用 markdown 表格，一条发完，先结论后要点。
