"""飞书通知脚本 — 由 GitHub Actions workflow 调用，环境变量传入参数。"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET

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


# ── 解析 JUnit XML，按文件聚合 ──

file_summary = []
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
        parts = cn.split(".")
        if len(parts) >= 2:
            fname = parts[0] + "/" + parts[1] + ".py"
        else:
            fname = cn.replace(".", "/") + ".py"

        if fname not in file_map:
            file_map[fname] = {"total": 0, "fail": 0, "error": 0, "skip": 0}
        file_map[fname]["total"] += 1
        if tc.find("failure") is not None:
            file_map[fname]["fail"] += 1
        if tc.find("error") is not None:
            file_map[fname]["error"] += 1
        if tc.find("skipped") is not None:
            file_map[fname]["skip"] += 1

    for fname, counts in sorted(file_map.items()):
        total_tests += counts["total"]
        total_failures += counts["fail"]
        total_errors += counts["error"]
        total_skipped += counts["skip"]

        has_issue = counts["fail"] + counts["error"] > 0
        icon = "❌" if has_issue else "✅"

        if has_issue:
            detail = f"{counts['total']} ran, {counts['fail']} failed"
            if counts["error"] > 0:
                detail += f", {counts['error']} errors"
            file_summary.append(f"{icon} `{fname}` — {detail}")
        else:
            file_summary.append(f"{icon} `{fname}` — {counts['total']} passed")

except Exception as e:
    file_summary = [f"⚠️ 无法解析测试报告: {e}"]


# ── 状态判定 ──

passed = TEST_EXIT_CODE == "0"
status_icon = "✅" if passed else "❌"
status_text = "PASSED" if passed else "FAILED"
color = "turquoise" if passed else "red"
summary_line = f"{total_tests} tests, {total_failures} failed, {total_skipped} skipped"


# ── Actions 日志：打印完整测试输出 ──

print("=" * 60)
print("📋 完整测试输出:")
print("=" * 60)
try:
    with open("test_output.txt", "r") as f:
        print(f.read())
except Exception:
    print("无法读取 test_output.txt")

if not passed:
    print("\n" + "=" * 60)
    print("❌ 失败用例详情:")
    print("=" * 60)
    try:
        tree = ET.parse("test_report.xml")
        root = tree.getroot()
        for tc in root.iter("testcase"):
            failure = tc.find("failure")
            if failure is not None:
                name = tc.get("name", "unknown")
                msg = failure.get("message") or ""
                text = (failure.text or "")[:500]
                print(f"\n--- {name} ---")
                print(f"Message: {msg}")
                print(text)
    except Exception as e:
        print(f"无法解析失败详情: {e}")

print("\n" + "=" * 60)
print("🔄 变更文件:")
print("=" * 60)
try:
    with open("changed_files.txt", "r") as f:
        print(f.read())
except Exception:
    print("无变更文件记录")


# ── 构建飞书卡片（精简版） ──

elements = [
    {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**同步**: ✅ 已推送\n"
                f"**触发**: {trigger_label}\n"
                f"**仓库**: {REPO} `master`\n"
                f"**测试汇总**: {summary_line}"
            ),
        },
    },
    {"tag": "hr"},
    {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": "**📋 测试结果**:\n" + "\n".join(file_summary),
        },
    },
    {"tag": "hr"},
    {
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "🔗 查看详情"},
            "url": run_url,
            "type": "primary",
        }],
    },
]

payload = {
    "timestamp": timestamp,
    "sign": sign,
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{status_icon} 同步+测试 - hermes-lark-streaming",
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