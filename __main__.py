"""CLI 入口: python -m hermes_lark_streaming [status|verify|cleanup].

兼容非标准安装路径：当 hermes_lark_streaming 不在默认 sys.path 时，
自动搜索常见安装路径并加入 sys.path。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_importable() -> None:
    """确保 hermes_lark_streaming 可被导入。

    非标准安装（如手动部署、Docker、自定义路径）时，包可能不在
    默认 sys.path 中。此函数搜索常见路径并加入 sys.path。
    """
    try:
        import hermes_lark_streaming  # noqa: F401
        return  # 已可导入，无需处理
    except ImportError:
        pass

    hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))

    # 搜索路径优先级
    search_paths: list[Path] = [
        # 1. HERMES_HOME/plugins/
        hermes_home / "plugins",
        # 2. HERMES_HOME 下的 site-packages（lib/python*/site-packages）
        *hermes_home.glob("lib/python*/site-packages"),
        # 3. hermes-agent 下的 site-packages
        *Path("/opt/hermes-agent").glob("lib/python*/site-packages"),
        *Path("/usr/local/hermes-agent").glob("lib/python*/site-packages"),
        *Path(str(Path.home() / "hermes-agent")).glob("lib/python*/site-packages"),
        # 4. 当前 Python 的 site-packages
        *Path(sys.prefix).glob("lib/python*/site-packages"),
        # 5. 插件目录（当 __main__.py 直接运行时）
        Path(__file__).resolve().parent,
    ]

    for p in search_paths:
        if p.exists() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
            try:
                import hermes_lark_streaming  # noqa: F401
                return  # 找到了，停止搜索
            except ImportError:
                continue


def main() -> int:
    _ensure_importable()

    args = sys.argv[1:]
    if not args:
        _print_usage()
        return 0

    cmd = args[0]

    if cmd == "status":
        return _cmd_status()
    if cmd == "verify":
        return _cmd_verify()
    if cmd == "cleanup":
        return _cmd_cleanup()

    print(f"Unknown command: {cmd}")
    _print_usage()
    return 1


def _print_usage() -> None:
    print("Usage: python -m hermes_lark_streaming <command>")
    print()
    print("Commands:")
    print("  status     Show current configuration and credentials status")
    print("  verify     Verify environment compatibility")
    print("  cleanup    Remove plugin-injected config from config.yaml (run after uninstall)")
    print()
    print("Note: This plugin uses runtime monkey patching (no file modification).")
    print("      Install/uninstall via: hermes plugins install/uninstall")


def _cmd_status() -> int:
    try:
        from .config import Config

        cfg = Config()
        print(f"Config hermes_lark_streaming.enabled: {cfg.enabled}")
        print(f"Config hermes_lark_streaming.linear: {cfg.linear}")
        print(f"Feishu credentials: {'configured' if (cfg.env_app_id or cfg.feishu_app_id) else 'MISSING'}")
        print()
        print("Plugin uses runtime monkey patching — no source files are modified.")
        print("Install/uninstall via: hermes plugins install/uninstall")
    except ImportError as e:
        print(f"Error: Cannot import hermes_lark_streaming: {e}")
        print("Please ensure the plugin is installed correctly.")
        return 1
    return 0


def _cmd_verify() -> int:
    try:
        from .config import Config

        cfg = Config()
        print(f"Config hermes_lark_streaming.enabled: {cfg.enabled}")
        print(f"Feishu credentials: {'configured' if (cfg.env_app_id or cfg.feishu_app_id) else 'MISSING'}")

        # Verify that gateway modules are importable
        try:
            from gateway.run import GatewayRunner
            print("gateway.run.GatewayRunner: importable")
        except ImportError as e:
            print(f"gateway.run.GatewayRunner: NOT importable ({e})")

        try:
            from run_agent import AIAgent
            print("run_agent.AIAgent: importable")
        except ImportError as e:
            print(f"run_agent.AIAgent: NOT importable ({e})")
    except ImportError as e:
        print(f"Error: Cannot import hermes_lark_streaming: {e}")
        print("Please ensure the plugin is installed correctly.")
        return 1

    return 0


def _cmd_cleanup() -> int:
    """Remove plugin-injected config entries from config.yaml.

    Run this after ``hermes plugins uninstall hermes-lark-streaming``
    to clean up the ``hermes_lark_streaming`` config section and ``plugins.enabled`` entry.
    """
    try:
        from .plugin import _cleanup_config

        _cleanup_config()
        print("Cleanup complete. Next steps:")
        print("  1. hermes plugins uninstall hermes-lark-streaming")
        print("  2. hermes gateway restart")
    except ImportError as e:
        print(f"Error: Cannot import hermes_lark_streaming: {e}")
        print("Please ensure the plugin is installed correctly.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
