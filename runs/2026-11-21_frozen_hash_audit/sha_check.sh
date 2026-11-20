#!/usr/bin/env bash
# 冻结文件 sha256 复核（2026-11-21）。只读，不改任何文件。在仓库根目录运行：bash runs/2026-11-21_frozen_hash_audit/sha_check.sh
chk(){ f=$1; exp=$2; [ -f "$f" ] || { echo "MISSING $f"; return; }
 lf=$(sha256sum "$f"|cut -d' ' -f1); crlf=$(sed 's/$/\r/' "$f"|sha256sum|cut -d' ' -f1)
 r=MISMATCH; [ "${lf:0:${#exp}}" = "$exp" ] && r=OK; [ $r = MISMATCH ] && [ "${crlf:0:${#exp}}" = "$exp" ] && r=OK_CRLF
 echo "$r $f exp=${exp:0:16} lf=${lf:0:16} crlf=${crlf:0:16}"; }
F=indicators/frozen; S=position/live/shadow
echo "## FROZEN_v1.json"; chk $F/run_shadow_v1.py e7a8e9b0fbbdcf6ead6a72e382da607b7f8bb985bd065d40680a44ed8d66ee06; chk $F/RULES_v1.md 46912b5d41e0430d771de913842e6cb285f3e7d86e200f5dc0343c648f9d4441; chk $F/universe_v1.csv 0539ca147dbb6dc829a59c3282ed0c6a1e652e9c462289aa9e1513b14260e884
echo "## FROZEN_v1_legacy.json"; chk $S/RULES_v1.md 46912b5d41e0430d771de913842e6cb285f3e7d86e200f5dc0343c648f9d4441; chk $S/engine.py 74d4c154c7fdb0acb807964c3b9859b126f76e9dc50ef3220a9faf3e7b73eca0; chk $S/run_shadow.py f82e47ad0d5c0f3c58b30223d98f032183a66315519a4a769a1e3e17157a5f55; chk $S/universe_v1.csv 0539ca147dbb6dc829a59c3282ed0c6a1e652e9c462289aa9e1513b14260e884
echo "## FROZEN_v2.json"; chk $S/RULES_v2.md 09221143852c05de68a1c624c1d2afe9c336423aefe519d07e3268b36dfda70c; chk $S/rules_v2.json acd6996af3835f881e0548daea17de97cbd66225d534e0a710fdcbfad78911b3; chk $S/run_shadow.py 2b4f3a0c2a2eb593a28874f326c87b73a0a0a185893622a99fa9879fd244fd13; chk $S/engine.py 74d4c154c7fdb0acb807964c3b9859b126f76e9dc50ef3220a9faf3e7b73eca0; chk $S/universe_v1.csv 0539ca147dbb6dc829a59c3282ed0c6a1e652e9c462289aa9e1513b14260e884
echo "## FROZEN_S4.md"; chk work/components_bt.py 819647d90f62e56d; chk work/components_core.py 3146eb133dd836f0
echo "## RULES_v2.md 去掉 2026-11-05 口径核对注后"; python3 - <<'PY'
import hashlib
t=open("position/live/shadow/RULES_v2.md",encoding="utf-8",newline="").read()
a=t.index("> **2026-11-05 口径核对"); b=t.index("要点（2026-11-04 base_ret 修复后重跑）：")
print(hashlib.sha256((t[:a]+t[b:]).encode()).hexdigest()[:16], "(期望 09221143852c05de)")
PY
