"""飞书通知脚本 — 由 GitHub Actions workflow 调用，环境变量传入参数。"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
#
# ── 从环境变量读取配置 ──

FEISHU_WEBHOOK = os.environ["FEISHU_WEBHOOK"]
FEISHU_SECRET = os.environ["FEISHU_SECRET"]
TEST_EXIT_CODE = os.environ.get("TEST_EXIT_CODE", "")
REPO = os.environ.get("REPO", "")
RUN_ID = os.environ.get("RUN_ID", "")
SERVER_URL = os.environ.get("SERVER_URL", "https://github.com")
TRIGGER = os.environ.get("TRIGGER", "")
FORCE = os.environ.get("FORCE", "false")

run_url = f"{SERVER_URL}/{REPO}/actions/runs/{RUN_ID}"
trigger_label = "⏰ 定时同步" if TRIGGER == "schedule" else "👆 手动触发"
if FORCE == "true":
    trigger_label += "（强制测试）"


# ── 飞书签名 ──

timestamp = str(int(time.time()))
string_to_sign = f"{timestamp}\n{FEISHU_SECRET}"
hmac_code = hmac.new(
    string_to_sign.encode("utf-8"),
    digestmod=hashlib.sha256,
).digest()
sign = base64.b64encode(hmac_code).decode("utf-8")


# ── 解析 JUnit XML 报告 ──

per_file_summary = []
total_tests = 0
total_failures = 0
total_errors = 0
total_skipped = 0

try:
    tree = ET.parse("test_report.xml")
    root = tree.getroot()

    file_map = {}
    for tc in root.iter("testcase"):
        cn = tc.get("classname", "unknown")
        short = cn.replace(".", "/") + ".py"
        if short not in file_map:
            file_map[short] = {"total": 0, "fail": 0, "error": 0, "skip": 0}
        file_map[short]["total"] += 1
        if tc.find("failure") is not None:
            file_map[short]["fail"] += 1
        if tc.find("error") is not None:
            file_map[short]["error"] += 1
        if tc.find("skipped") is not None:
            file_map[short]["skip"] += 1

    for fname, counts in sorted(file_map.items()):
        icon = "❌" if counts["fail"] + counts["error"] > 0 else "✅"
        parts = [f"{counts['total']} ran"]
        if counts["fail"] > 0:
            parts.append(f"{counts['fail']} failed")
        if counts["error"] > 0:
            parts.append(f"{counts['error']} errors")
        if counts["skip"] > 0:
            parts.append(f"{counts['skip']} skipped")
        per_file_summary.append(f"{icon} `{fname}` — {', '.join(parts)}")

    total_tests = sum(c["total"] for c in file_map.values())
    total_failures = sum(c["fail"] for c in file_map.values())
    total_errors = sum(c["error"] for c in file_map.values())
    total_skipped = sum(c["skip"] for c in file_map.values())

except Exception as e:
    per_file_summary = [f"⚠️ 无法解析测试报告: {e}"]


# ── 状态判定 ──

passed = TEST_EXIT_CODE == "0"
status_icon = "✅" if passed else "❌"
status_text = "PASSED" if passed else "FAILED"
color = "turquoise" if passed else "red"
summary_line = (
    f"{total_tests} tests, {total_failures} failures, "
    f"{total_errors} errors, {total_skipped} skipped"
)


# ── 读取变更文件 ──

changed_files = ""
try:
    with open("changed_files.txt", "r") as f:
        changed_files = f.read().strip()[:800]
except Exception:
    pass


# ── 读取失败详情 ──

failure_details = ""
if not passed:
    try:
        with open("test_output.txt", "r") as f:
            lines = f.readlines()
            failure_lines = []
            for i, line in enumerate(lines):
                if "FAILED" in line:
                    start = max(0, i - 1)
                    end = min(len(lines), i + 6)
                    failure_lines.extend(lines[start:end])
                    failure_lines.append("...")
            failure_details = "".join(failure_lines[-30:]).replace("`", "'")
    except Exception:
        failure_details = "无法读取失败详情"


# ── 构建飞书卡片 ──

elements = [
    {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**同步**: ✅ 新代码已推送\n"
                f"**测试**: {status_icon} {status_text}\n"
                f"**触发**: {trigger_label}\n"
                f"**仓库**: {REPO} `master`\n"
                f"**汇总**: {summary_line}"
            ),
        },
    },
    {"tag": "hr"},
    {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": "**📋 各脚本结果**:\n" + "\n".join(per_file_summary),
        },
    },
]

if changed_files:
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**🔄 变更文件**:\n{changed_files}",
        },
    })

if failure_details:
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**❌ 失败详情**:\n```\n{failure_details[:1500]}\n```",
        },
    })

elements.append({"tag": "hr"})
elements.append({
    "tag": "action",
    "actions": [{
        "tag": "button",
        "text": {"tag": "plain_text", "content": "🔗 查看 Actions 详情"},
        "url": run_url,
        "type": "primary",
    }],
})

payload = {
    "timestamp": timestamp,
    "sign": sign,
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{status_icon} 同步+测试报告 - hermes-lark-streaming",
            },
            "template": color,
        },
        "elements": elements,
    },
}


# ── 发送请求 ──

data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(
    FEISHU_WEBHOOK,
    data=data,
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req) as resp:
        print(f"✅ Feishu notified: {resp.read().decode()}")
except Exception as e:
    print(f"❌ Failed to notify Feishu: {e}")