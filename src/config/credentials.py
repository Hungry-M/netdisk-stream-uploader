# src/config/credentials.py
"""
百度网盘凭证管理。

加载优先级（从高到低）：
1. 环境变量 BAIDU_CLIENT_ID / BAIDU_CLIENT_SECRET
2. ~/.netdisk_uploader/credentials.json（不会进 git）
3. 首次运行交互输入，并保存到上述文件

注意：凭证永远不应该硬编码在源码里，也不应该提交到版本控制。
"""
import json
import os
import sys
from src.logger import get_logger
from pathlib import Path
from typing import Optional

logger = get_logger(__name__)


class Credentials:
    def __init__(self, client_id: str, client_secret: str,
                 token_file: Optional[str] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_file = token_file


def _credentials_path() -> Path:
    from src.paths import data_file

    return data_file("credentials.json")


def load_credentials(prompt_if_missing: bool = True) -> Credentials:
    """
    加载百度凭证。
    环境变量优先级最高，本地文件次之，最后交互式输入并保存。
    """
    # 1. 环境变量
    env_id = os.environ.get("BAIDU_CLIENT_ID")
    env_secret = os.environ.get("BAIDU_CLIENT_SECRET")
    if env_id and env_secret:
        logger.info("从环境变量加载百度凭证")
        return Credentials(env_id, env_secret, os.environ.get("BAIDU_TOKEN_FILE"))

    # 2. 本地文件
    cred_path = _credentials_path()
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            if data.get("client_id") and data.get("client_secret"):
                logger.info(f"从 {cred_path} 加载百度凭证")
                return Credentials(
                    data["client_id"],
                    data["client_secret"],
                    data.get("token_file"),
                )
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"读取 {cred_path} 失败: {e}")

    # 3. 交互式输入
    if not prompt_if_missing:
        raise RuntimeError(
            f"未找到百度凭证。请设置环境变量 BAIDU_CLIENT_ID / "
            f"BAIDU_CLIENT_SECRET，或在 {cred_path} 中配置。"
        )

    # 没有可交互的终端时不能去 input()：会永久阻塞（CI、被重定向的 stdout、
    # 某些 IDE 的运行窗口）。宁可报错让用户显式设置。
    if not sys.stdin or not sys.stdin.isatty():
        raise RuntimeError(
            "未找到百度凭证，且当前不是交互式终端，无法提示输入。\n"
            "请在能输入的终端里运行，或设置 BAIDU_CLIENT_ID / BAIDU_CLIENT_SECRET。"
        )

    import click
    print(f"\n首次运行，请输入百度网盘应用凭证")
    print(f"（只保存在本机：{cred_path}）\n")
    client_id = click.prompt("AppKey").strip()
    client_secret = click.prompt("SecretKey").strip()

    save_credentials(client_id, client_secret)
    print(f"✅ 凭证已保存到 {cred_path}\n")

    _ask_app_name()
    return Credentials(client_id, client_secret)


def _ask_app_name() -> None:
    """
    顺便问一次应用名，而不是以后让用户去填整条网盘路径。

    百度只允许写 /apps/<应用名>，所以应用名是路径的唯一来源；
    在 OAuth 这一步顺便声明，比事后改配置合理。
    """
    import click

    try:
        from src.config.user_config import build_settings, set_value
        current = build_settings().app_name or "StreamPack"
    except Exception:
        return

    print("你的应用名决定网盘可写目录：/apps/<应用名>")
    print(f"直接回车沿用 {current}；注册时填的是别的名字再输入。")
    app_name = click.prompt("应用名", default="", show_default=False).strip()
    if not app_name:
        app_name = current
    if app_name == current:
        return

    try:
        set_value("app_name", app_name)
        print(f"✅ 应用名已保存，上传目录为 /apps/{app_name}\n")
    except ValueError as e:
        print(f"⚠️ 应用名无效，沿用 {current}：{e}\n")


def save_credentials(client_id: str, client_secret: str) -> Path:
    """保存凭证到本地文件（供交互输入后调用）"""
    cred_path = _credentials_path()
    cred_path.write_text(
        json.dumps(
            {"client_id": client_id, "client_secret": client_secret},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return cred_path