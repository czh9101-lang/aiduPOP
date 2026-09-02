"""WP-1 守卫：版本单一真相源（SOP 铁律 + 挨骂清单坑6「版本号多点不一致」累犯）。

判据用「解析真实值再比」，不 grep 字符串（记忆 feedback_guard_judgment_ast_not_string）：
从 plugin.yaml 读出**当前版本** V，断言 V 这个字面量只出现在真相源与历史叙述里，
不出现在任何代码/前端/README 徽章里。升版时 V 自动跟随，守卫无需同步改。

为什么只锁「当前版本」而不锁历史版本号：
  `v2.3.1: 备份轮转` 这类注释描述的是**当时**发生的事，合法且必须保留（抹了就是篡改项目史）。
  病灶是「当前版本声明」散落多处 —— 那才会随升版而互相打架。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# 允许出现「当前版本」字面量的文件（真相源 + 历史叙述 + 本守卫自身）。
_ALLOWED = {
    "plugin.yaml",              # 单一真相源
    "docs/CHANGELOG.md",        # 历史叙述：每个版本的发布说明
    "tests/test_version_single_source.py",  # 本守卫（读逻辑里会提到字段名）
}

def _current_version() -> str:
    for line in (_REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    raise AssertionError("plugin.yaml 无 version: 字段，真相源本身坏了")

def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    files = []
    for rel in out:
        p = _REPO_ROOT / rel
        if not p.is_file():
            continue
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
                                ".woff", ".woff2", ".ttf", ".gz", ".zip"}:
            continue
        files.append(p)
    return files

def test_current_version_not_hardcoded_outside_source() -> None:
    v = _current_version()
    assert v and v != "unknown", f"真相源读出的版本不可信：{v!r}"
    offenders: list[str] = []
    for p in _tracked_text_files():
        rel = str(p.relative_to(_REPO_ROOT))
        if rel in _ALLOWED:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # 精确匹配版本字面量（带词边界，避免 2.4.0.1 之类误伤，也避免把 12.4.0 当命中）
        if _contains_version(text, v):
            offenders.append(rel)
    assert not offenders, (
        f"当前版本 {v!r} 被硬编码在真相源之外（SOP 铁律违反，坑6 累犯）：\n  "
        + "\n  ".join(offenders)
        + "\n改为从 plugin.yaml 读取（Python: from .. import __version__；"
          "前端: /api/health；README: shields dynamic badge）。"
    )

def _contains_version(text: str, v: str) -> bool:
    """词边界匹配：v 前后不能是数字或点，否则 2.4.0 会误命中 12.4.0 / 2.4.0.1。"""
    import re
    return re.search(rf"(?<![\d.]){re.escape(v)}(?![\d.])", text) is not None

def test_negative_control_guard_has_teeth() -> None:
    """负向对照：守卫必须真的能抓到硬编码，否则它报「通过」与「坏了」长得一样。
    造一个含当前版本的临时文件喂给判据函数，必须判为命中。"""
    v = _current_version()
    assert _contains_version(f'server_version = "aiduPOP-Studio/{v}"', v), \
        "守卫判据对硬编码不敏感 —— 假绿灯，不可信"
    # 反向：历史版本号不该被当前版本判据命中
    assert not _contains_version("# v2.3.1: 备份轮转", v), \
        "守卫把历史叙述误判为当前版本硬编码 —— 假红灯"
