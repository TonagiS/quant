"""两边额度都紧张：再压一轮上下文和调用次数。"""

import json
import shutil
import time
from pathlib import Path

BASE = Path(r"E:\AI\AstrBot-data\.astrbot_launcher\instances\8403a513-d122-41b6-88d8-754c223ac2ba\core\data")
BAK = Path(r"E:\Code\quant\work\bak")
stamp = time.strftime("%Y%m%d_%H%M%S")
BAK.mkdir(parents=True, exist_ok=True)


def patch(path: Path, changes: dict, label: str) -> None:
    if not path.exists():
        print(f"[{label}] 缺文件 {path.name}")
        return
    shutil.copy2(path, BAK / f"{path.name}.bak_save_{stamp}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    print(f"\n[{label}]")
    for dotted, value in changes.items():
        node = data
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        old = node.get(parts[-1])
        node[parts[-1]] = value
        print(f"  {dotted}: {old} -> {value}")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


patch(
    BASE / "cmd_config.json",
    {
        "provider_ltm_settings.group_message_max_cnt": 6,
        "provider_ltm_settings.group_message_history_max_cnt": 30,
    },
    "群上下文",
)
patch(
    BASE / "config" / "astrbot_plugin_group_profile_config.json",
    {"max_people": 5, "inject_chars": 300, "trigger_messages": 300},
    "群成员档案",
)
patch(
    BASE / "config" / "astrbot_plugin_ds_meme_boost_config.json",
    {"max_judge_per_hour": 3},
    "表情加权收录",
)
patch(
    BASE / "config" / "astrbot_plugin_ds_alert_config.json",
    {"probability": 0.35, "max_per_hour": 1},
    "ds 关键词接话",
)
print("\n备份:", BAK / f"*.bak_save_{stamp}")
