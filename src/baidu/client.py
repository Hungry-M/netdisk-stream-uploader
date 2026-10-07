# src/baidu/client.py
import requests
import json
import logging
from urllib.parse import quote
from typing import Dict, Optional, BinaryIO

from src.baidu.auth import BaiduOAuth

logger = logging.getLogger(__name__)

# 普通 API 基础地址（用于非上传操作）
BAIDU_API_BASE = "https://pan.baidu.com/rest/2.0/pcs"
# 获取上传域名时的固定地址
LOCATE_UPLOAD_URL = "https://d.pcs.baidu.com/rest/2.0/pcs/file"


class BaiduClient:
    """
    百度网盘 API 客户端。
    - 集成 OAuth 授权，自动管理 access_token
    - 上传前动态获取上传域名
    """

    def __init__(self, client_id: str, client_secret: str,
                 token_file: Optional[str] = None):
        self.oauth = BaiduOAuth(client_id, client_secret, token_file)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'pan.baidu.com',
            'Accept-Encoding': 'identity',
        })
        self._upload_domain_cache = None  # 缓存上传域名

    def _get_access_token(self) -> str:
        return self.oauth.get_valid_access_token()

    def _request(self, method: str, url: str, params: Optional[Dict] = None,
                 data: Optional[Dict] = None, files: Optional[Dict] = None) -> Dict:
        token = self._get_access_token()
        if params is None:
            params = {}
        params['access_token'] = token

        try:
            resp = self.session.request(method, url, params=params,
                                        data=data, files=files,
                                        timeout=(10, 300))  # 连接10s, 读取300s
            resp.raw.decode_content = False
            resp.raise_for_status()
            json_data = resp.json()
            if isinstance(json_data, dict) and json_data.get('error_code', 0) != 0:
                error_msg = json_data.get('error_msg', '未知错误')
                raise Exception(
                    f"百度 API 错误 ({json_data.get('error_code')}): {error_msg}"
                )
            return json_data
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.warning("Token 无效，尝试刷新...")
                self.oauth._token_data = None
                token = self._get_access_token()
                params['access_token'] = token
                resp = self.session.request(method, url, params=params,
                                            data=data, files=files,
                                            timeout=(10, 300))
                resp.raise_for_status()
                return resp.json()
            raise

    # ---------- 获取上传域名 ----------

    def locate_upload_domain(self, path: str, upload_id: str) -> str:
        """
        获取当前可用的上传域名。
        分片上传和单步上传必须使用此接口返回的域名，不能固定写死。

        Args:
            path: 上传后的文件绝对路径，如 /apps/StreamPack/file.zip
            upload_id: 预上传接口返回的 uploadid
        Returns:
            可用的上传服务地址（HTTPS）
        """
        params = {
            'method': 'locateupload',
            'appid': 250528,
            'access_token': self._get_access_token(),
            # 'path': quote(path, safe=''),  # 移除 quote 调用
            'path': path,                     # 直接传递原始路径
            'uploadid': upload_id,
            'upload_version': '2.0',
        }
        resp = self.session.get(LOCATE_UPLOAD_URL, params=params, timeout=(10, 60))
        resp.raise_for_status()
        data = resp.json()

        if data.get('error_code', 0) != 0:
            raise Exception(
                f"获取上传域名失败 ({data.get('error_code')}): "
                f"{data.get('error_msg', '未知错误')}"
            )

        servers = data.get('servers', [])
        if not servers:
            raise RuntimeError(f"无可用上传服务器: {data}")

        # 优先使用 HTTPS 地址
        for s in servers:
            server = s.get('server', '')
            if server.startswith('https://'):
                logger.info(f"获取到上传域名: {server}")
                return server.rstrip('/')

        # 回退：使用第一个
        server = servers[0].get('server', '').rstrip('/')
        logger.info(f"获取到上传域名(HTTP): {server}")
        return server

    # ---------- 文件上传 ----------

    def upload_file(self, remote_path: str, local_file: BinaryIO,
                    upload_domain: Optional[str] = None) -> Dict:
        """
        上传文件（适用于 < 4GB 的文件）。
        如果未提供 upload_domain，则自动获取。
        """
        if not upload_domain:
            upload_domain = "https://c.pcs.baidu.com"

        url = f"{upload_domain}/rest/2.0/pcs/file"
        params = {
            'method': 'upload',
            # 'path': quote(remote_path, safe=''),  # 移除 quote 调用
            'path': remote_path,                     # 直接传递原始路径
            'ondup': 'overwrite',
        }
        files = {'file': (remote_path.split('/')[-1], local_file)}
        return self._request('POST', url, params=params, files=files)

    # ---------- 分片上传（大文件） ----------

    def precreate(self, path: str, size: int, block_list: list) -> Dict:
        """预创建大文件，返回 uploadid"""
        url = f"{BAIDU_API_BASE}/file"
        params = {
            'method': 'precreate',
            'path': quote(path, safe=''),
            'size': size,
            'block_list': json.dumps(block_list),
            'isdir': '0',
            'autoinit': '1',
        }
        return self._request('POST', url, params=params)

    def upload_part(self, upload_id: str, part_seq: int,
                    data: bytes, upload_domain: str) -> Dict:
        """
        上传分片（使用动态获取的域名）。
        """
        url = f"{upload_domain}/rest/2.0/pcs/superfile2"
        params = {
            'method': 'upload',
            'type': 'tmpfile',
            'uploadid': upload_id,
            'partseq': part_seq,
        }
        files = {'file': (f'part_{part_seq}', data)}
        return self._request('POST', url, params=params, files=files)

    def create_file(self, upload_id: str) -> Dict:
        """合并分片完成上传"""
        url = f"{BAIDU_API_BASE}/file"
        params = {
            'method': 'create',
            'uploadid': upload_id,
        }
        return self._request('POST', url, params=params)

    # ---------- 其他操作 ----------

    def list_files(self, remote_dir: str) -> Dict:
        url = f"{BAIDU_API_BASE}/file"
        params = {
            'method': 'list',
            'dir': remote_dir,
            'order': 'time',
            'desc': '1',
        }
        return self._request('GET', url, params=params)

    def delete_file(self, remote_path: str) -> Dict:
        url = f"{BAIDU_API_BASE}/file"
        params = {
            'method': 'delete',
            'path': remote_path,
        }
        return self._request('POST', url, params=params)
