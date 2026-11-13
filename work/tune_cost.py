"""降 token：缩上下文、缩档案、关情绪分析、降各插件的判定次数。"""

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
        print(f"[{label}] 缺文件，跳过 {path.name}")
        return
    shutil.copy2(path, BAK / f"{path.name}.bak_cost_{stamp}")
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


# 1) 群上下文：注入的历史条数砍半，缓存条数也砍
patch(
    BASE / "cmd_config.json",
    {
        "provider_ltm_settings.group_message_max_cnt": 10,
        "provider_ltm_settings.group_message_history_max_cnt": 40,
        "provider_ltm_settings.image_caption": False,
    },
    "AstrBot 群上下文",
)

# 2) 表情包插件的"自然情绪分析"：每条回复一次模型调用，关掉
patch(
    BASE / "config" / "astrbot_plugin_stealer_config.json",
    {"enable_natural_emotion_analysis": False, "steal_chance": 0.2},
    "stealer",
)

# 3) ds 关键词接话：看图更省、每小时更少
patch(
    BASE / "config" / "astrbot_plugin_ds_alert_config.json",
    {"image_probability": 0.12, "max_per_hour": 1},
    "ds_alert",
)

# 4) 表情包加权收录：判定次数砍一半以上
patch(
    BASE / "config" / "astrbot_plugin_ds_meme_boost_config.json",
    {"max_judge_per_hour": 5, "cooldown_seconds": 40},
    "ds_meme_boost",
)

# 5) 无 @ 接话判定：每小时次数降到 5
patch(
    BASE / "config" / "astrbot_plugin_reply_guess_config.json",
    {"judge_max_per_hour": 5},
    "reply_guess",
)

# 6) 群成员档案：注入更短、更新更稀疏
patch(
    BASE / "config" / "astrbot_plugin_group_profile_config.json",
    {"max_people": 8, "inject_chars": 400, "trigger_messages": 250},
    "group_profile",
)
print("\n备份:", BAK / f"*.bak_cost_{stamp}")
