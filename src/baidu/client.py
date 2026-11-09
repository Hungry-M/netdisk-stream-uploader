# src/baidu/client.py
"""
百度网盘 API 客户端。

关于"没有实现的东西"，这里写清楚，免得后人又加回来：

- **分片上传**（precreate / upload_part / create）：目前用不到。分卷默认 1 GB，
  远低于 PCS 单次上传上限（约 4 GB），一次 `method=upload` 就能传完。
- **动态上传域名**（method=locateupload）：它需要 `uploadid`，而 `uploadid`
  只能由 `precreate` 产生——也就是说动态域名必须建立在整套分片流程之上。
  在单次上传这条路上它不可用，所以没有实现，上传域名固定为 `c.pcs.baidu.com`。

如果将来真要做 > 4 GB 的单卷，这两件事要一起做，那时候再加。
"""
import json
from typing import BinaryIO, Dict, Optional

import requests

from src.logger import get_logger
from src.baidu.auth import BaiduOAuth

logger = get_logger(__name__)

# 单次上传用的 PCS 域名。不用 locateupload，原因见模块 docstring。
PCS_UPLOAD_BASE = "https://c.pcs.baidu.com"
# 文件管理类操作（list / filemanager）走 xpan，与上传用的 PCS 不是同一套
XPAN_API_BASE = "https://pan.baidu.com/rest/2.0/xpan"


class BaiduClient:
    """百度网盘 API 客户端，集成 OAuth 与 access_token 自动刷新。"""

    def __init__(self, client_id: str, client_secret: str,
                 token_file: Optional[str] = None):
        self.oauth = BaiduOAuth(client_id, client_secret, token_file)
        from src.net import new_session

        self.session = new_session()
        self.session.headers.update({
            'User-Agent': 'pan.baidu.com',
            'Accept-Encoding': 'identity',
        })

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
            if isinstance(json_data, dict):
                # PCS 用 error_code，xpan 用 errno，两种都要认
                err = json_data.get('error_code', json_data.get('errno', 0)) or 0
                if err != 0:
                    error_msg = (json_data.get('error_msg')
                                 or json_data.get('errmsg')
                                 or '未知错误')
                    raise Exception(f"百度 API 错误 ({err}): {error_msg}")
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

    # ---------- 文件上传 ----------

    def upload_file(self, remote_path: str, local_file: BinaryIO,
                    upload_domain: Optional[str] = None) -> Dict:
        """
        单次上传一个文件（适用于 < 4 GB）。

        路径不做 percent-encoding：实测编码后百度会把 %2F 当字面量处理，
        反而导致 403。
        """
        base = upload_domain or PCS_UPLOAD_BASE
        url = f"{base}/rest/2.0/pcs/file"
        params = {
            'method': 'upload',
            'path': remote_path,
            'ondup': 'overwrite',
        }
        files = {'file': (remote_path.split('/')[-1], local_file)}
        return self._request('POST', url, params=params, files=files)

    # ---------- 文件管理 ----------
    #
    # 这两个方法必须走 xpan 接口。老的 PCS 接口只保留了上传相关 method，
    # 用旧端点请求 list / delete 会直接返回 404。

    def list_files(self, remote_dir: str, limit: int = 1000) -> Dict:
        url = f"{XPAN_API_BASE}/file"
        params = {
            'method': 'list',
            'dir': remote_dir,
            'order': 'name',
            'start': 0,
            'limit': limit,
        }
        return self._request('GET', url, params=params)

    def delete_file(self, remote_path: str) -> Dict:
        url = f"{XPAN_API_BASE}/file"
        params = {
            'method': 'filemanager',
            'opera': 'delete',
            'async': '0',
        }
        # filelist 必须放在 POST 请求体里。放在 query 里接口只回 errno 2。
        data = {'filelist': json.dumps([remote_path], ensure_ascii=False)}
        result = self._request('POST', url, params=params, data=data)

        failed = [i for i in (result.get('info') or []) if i.get('errno')]
        if failed:
            item = failed[0]
            raise Exception(
                f"删除失败: {item.get('path')} (errno {item.get('errno')})"
            )
        return result
