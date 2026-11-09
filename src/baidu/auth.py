# src/baidu/auth.py
import json
from src.logger import get_logger
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlencode

import requests

logger = get_logger(__name__)

BAIDU_OAUTH_BASE = "https://openapi.baidu.com/oauth/2.0"
AUTHORIZE_URL = f"{BAIDU_OAUTH_BASE}/authorize"
TOKEN_URL = f"{BAIDU_OAUTH_BASE}/token"


class BaiduOAuth:
    """
    百度 OAuth 2.0 授权码模式（OOB 回调）。
    适用于无服务器端的本地应用。
    """

    def __init__(self, client_id: str, client_secret: str,
                 token_file: Optional[str] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        if token_file is None:
            from src.paths import data_file

            self.token_file = data_file("token.json")
        else:
            self.token_file = Path(token_file)
            self.token_file.parent.mkdir(exist_ok=True)

        self._token_data = None

    # ---------- 内部请求 ----------

    def _post(self, url: str, data: Dict) -> Dict:
        """发送 POST 请求并解析 JSON 响应，错误时抛出带详细信息的异常"""
        from src.net import post as net_post

        resp = net_post(url, data=data, timeout=15)
        try:
            json_data = resp.json()
        except ValueError:
            raise Exception(f"OAuth 响应非 JSON (HTTP {resp.status_code}): {resp.text[:300]}")

        if "error" in json_data:
            raise Exception(
                f"OAuth 错误: {json_data['error']} - "
                f"{json_data.get('error_description', '')} (HTTP {resp.status_code})"
            )
        if resp.status_code >= 400:
            raise Exception(f"OAuth HTTP {resp.status_code}: {json_data}")
        return json_data

    # ---------- 授权流程 ----------

    def _build_authorize_url(self) -> str:
        """生成用户授权链接"""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": "oob",
            "scope": "basic,netdisk",
            "display": "popup",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def _exchange_code_for_token(self, code: str) -> Dict:
        """用授权码换取 Access Token"""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": "oob",
        }
        return self._post(TOKEN_URL, data)

    def _do_interactive_auth(self) -> str:
        """引导用户完成 OOB 授权，返回 access_token"""
        url = self._build_authorize_url()
        print("\n" + "=" * 60)
        print("请用浏览器打开以下链接并完成授权：")
        print(url)
        print("=" * 60)
        print("授权成功后，页面会显示一串授权码（code）。")
        print("请将授权码完整复制并粘贴到下方。\n")

        code = input("请输入授权码: ").strip()
        if not code:
            raise Exception("未输入授权码，授权取消")

        token_data = self._exchange_code_for_token(code)
        self._token_data = token_data
        self._save_tokens(token_data)
        return token_data["access_token"]

    # ---------- 刷新 ----------

    def refresh_access_token(self, refresh_token: str) -> Dict:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        resp = self._post(TOKEN_URL, data)
        self._token_data = resp
        self._save_tokens(resp)
        return resp

    # ---------- 令牌持久化 ----------

    def _save_tokens(self, token_data: Dict):
        token_data["timestamp"] = time.time()
        with open(self.token_file, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)
        logger.info("令牌已保存")

    def load_tokens(self) -> Optional[Dict]:
        if self.token_file.exists():
            with open(self.token_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    # ---------- 对外入口 ----------

    def get_valid_access_token(self, interactive: bool = True) -> str:
        """
        获取有效 token：内存 → 文件 → 刷新 → 重新授权。

        interactive=False 时不会阻塞等待用户粘贴授权码，刷新也失败就抛
        RuntimeError —— 供 GUI、离线预演等无人值守场景使用。
        """
        # 1. 内存缓存
        if self._token_data and "access_token" in self._token_data:
            if self._is_valid(self._token_data):
                return self._token_data["access_token"]

        # 2. 文件
        token_data = self.load_tokens()
        if token_data:
            if self._is_valid(token_data):
                self._token_data = token_data
                return token_data["access_token"]
            if "refresh_token" in token_data:
                try:
                    new_token = self.refresh_access_token(token_data["refresh_token"])
                    return new_token["access_token"]
                except Exception as e:
                    logger.warning(f"刷新令牌失败: {e}，将重新授权")

        # 3. 重新授权
        if not interactive:
            raise RuntimeError("access_token 已失效且无法刷新，需要重新授权")
        return self._do_interactive_auth()

    @staticmethod
    def _is_valid(token_data: Dict) -> bool:
        if "access_token" not in token_data:
            return False
        if "expires_in" not in token_data:
            return True
        expire_at = token_data.get("timestamp", 0) + token_data["expires_in"] - 60
        return time.time() < expire_at
