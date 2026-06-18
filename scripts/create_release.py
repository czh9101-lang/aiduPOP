#!/usr/bin/env python3
"""Gitee Go 流水线发版脚本 —— 创建 git tag + 调用 Gitee API 创建 Release.

在工作流中由 .workflow/release-pipeline.yml 调用。所有变量通过环境
变量传入（shell 自己展开，不依赖 release@gitee 插件的参数模板引擎）：

必需环境变量：
    GITEE_OWNER  仓库 owner（如 Aowen-Nowor）
    GITEE_TOKEN  个人访问令牌（需 projects、releases 权限）

可选环境变量：
    REPO         仓库名（默认 hermes-lark-streaming）
    TARGET_BRANCH Release 的 target_commitish（默认 github_sync）

版本号从 plugin.yaml 提取（grep + sed，不依赖 PyYAML）。
CHANGELOG.md 内容作为 Release body（顶部加完整更新日志链接，用户点击可跳转）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_YAML = REPO_ROOT / "plugin.yaml"
CHANGELOG_MD = REPO_ROOT / "docs" / "CHANGELOG.md"

GITEE_API_BASE = "https://gitee.com/api/v5/repos"


def extract_version() -> str:
    """从 plugin.yaml 提取 version 字段，兼容单引号/双引号/无引号三种写法."""
    if not PLUGIN_YAML.exists():
        print(f"ERROR: {PLUGIN_YAML} 不存在", file=sys.stderr)
        sys.exit(1)
    for line in PLUGIN_YAML.read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            # 用 grep+sed 同款逻辑，保持与 shell 实现一致
            value = line.split(":", 1)[1].strip()
            value = value.strip('"').strip("'")
            return value
    print("ERROR: plugin.yaml 中未找到 version: 字段", file=sys.stderr)
    sys.exit(1)


def ensure_env() -> tuple[str, str, str, str]:
    """校验并返回 (owner, token, repo, target_branch)."""
    owner = os.environ.get("GITEE_OWNER", "").strip()
    token = os.environ.get("GITEE_TOKEN", "").strip()
    repo = os.environ.get("REPO", "hermes-lark-streaming").strip()
    target = os.environ.get("TARGET_BRANCH", "github_sync").strip()

    if not owner:
        print(
            "ERROR: 环境变量 GITEE_OWNER 未配置\n"
            "请在 Gitee Go 流水线设置 → 通用变量 中配置 GITEE_OWNER=Aowen-Nowor",
            file=sys.stderr,
        )
        sys.exit(1)
    if not token:
        print(
            "ERROR: 环境变量 GITEE_TOKEN 未配置\n"
            "请在 Gitee Go 流水线设置 → 通用变量 中配置 GITEE_TOKEN=<个人访问令牌>",
            file=sys.stderr,
        )
        sys.exit(1)
    return owner, token, repo, target


def create_git_tag(tag: str) -> None:
    """创建并推送 git tag（已存在则跳过）."""
    # 检查 tag 是否已存在
    result = subprocess.run(
        ["git", "rev-parse", tag],
        capture_output=True,
    )
    if result.returncode == 0:
        print(f"ℹ️  tag {tag} 已存在，跳过创建")
        return

    # 配置 git 身份
    subprocess.run(["git", "config", "user.email", "ci@gitee.com"], check=True)
    subprocess.run(["git", "config", "user.name", "gitee-go"], check=True)

    # 创建并推送 tag
    subprocess.run(["git", "tag", tag], check=True)
    subprocess.run(["git", "push", "origin", tag], check=True)
    print(f"✅ 已创建并推送 tag {tag}")


def build_release_body(tag: str, owner: str, repo: str) -> str:
    """构造 Release 描述：版本号 + 完整 CHANGELOG 链接 + CHANGELOG 内容."""
    changelog_url = f"https://gitee.com/{owner}/{repo}/raw/github_sync/docs/CHANGELOG.md"

    # 读取 CHANGELOG.md 内容（截断到 60000 字符避免 API 拒绝）
    try:
        changelog_body = CHANGELOG_MD.read_text(encoding="utf-8")[:60000]
    except Exception as e:
        changelog_body = f"（读取 CHANGELOG.md 失败: {e}）"

    return (
        f"{tag} 发行版\n"
        f"\n"
        f"完整更新日志：{changelog_url}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"{changelog_body}"
    )


def create_gitee_release(
    *,
    owner: str,
    token: str,
    repo: str,
    tag: str,
    body: str,
    target_branch: str,
) -> None:
    """调用 Gitee Open API 创建 Release.

    API 文档: https://gitee.com/api/v5/swagger
    """
    url = f"{GITEE_API_BASE}/{owner}/{repo}/releases"
    payload = {
        "access_token": token,
        "tag_name": tag,
        "name": tag,
        "body": body,
        "prerelease": False,
        "target_commitish": target_branch,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        method="POST",
    )

    print("ℹ️  调用 Gitee API 创建 Release...")
    print(f"   URL: {url}")
    print(f"   tag: {tag}")
    print(f"   name: {tag}")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp_body = resp.read().decode("utf-8")
            status = resp.status
            print(f"✅ API 响应码: {status}")
            try:
                data = json.loads(resp_body)
                release_url = data.get("html_url", "")
                if release_url:
                    print("✅ Release 创建成功！")
                    print(f"   URL: {release_url}")
            except json.JSONDecodeError:
                print("✅ Release 创建成功（响应非 JSON）")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        # 已存在的 Release 视为成功
        lower = err_body.lower()
        if "已存在" in err_body or "already exist" in lower or "exist" in lower:
            print(f"ℹ️  Release {tag} 已存在，跳过创建")
        else:
            print(f"❌ Release 创建失败，HTTP {e.code}", file=sys.stderr)
            print(f"   响应: {err_body[:2000]}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"❌ 调用 API 异常: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    # 1. 提取版本号
    version = extract_version()
    tag = f"v{version}"
    print(f"✅ 检测到版本号: {version}  →  tag: {tag}")

    # 2. 校验环境变量
    owner, token, repo, target = ensure_env()
    print(f"✅ 仓库: {owner}/{repo}")

    # 3. 创建 git tag
    create_git_tag(tag)

    # 4. 构造 Release 描述
    body = build_release_body(tag, owner, repo)

    # 5. 调用 Gitee API 创建 Release
    create_gitee_release(
        owner=owner, token=token, repo=repo,
        tag=tag, body=body, target_branch=target,
    )

    print("✅ 发版流水线完成")


if __name__ == "__main__":
    main()
