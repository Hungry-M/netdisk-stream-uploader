# src/net.py
"""
网络出口的集中开关。

为什么不改环境变量：`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 属于用户环境，
程序不该改。这里只提供一个**本次运行**的直连开关（`--no-proxy`），
让系统代理坏掉时还能跑一次。
"""
import requests

_DIRECT = False


def set_direct(value: bool) -> None:
    """本次运行是否绕过系统代理/环境变量直连。"""
    global _DIRECT
    _DIRECT = bool(value)


def is_direct() -> bool:
    return _DIRECT


def new_session() -> requests.Session:
    """新建 Session；直连模式下不读取任何代理配置。"""
    session = requests.Session()
    if _DIRECT:
        session.trust_env = False
    return session


def post(url: str, **kwargs):
    """与 requests.post 等价，但遵守直连开关。"""
    session = new_session()
    try:
        return session.post(url, **kwargs)
    finally:
        session.close()


def proxy_hint(exc: Exception) -> str:
    """
    把代理类错误翻译成一句人能照着做的事。

    典型来源：系统代理开着但代理软件没启动，requests 会抛
    ProxyError / SSLEOFError，原始信息对用户毫无意义。
    """
    text = str(exc)
    if isinstance(exc, requests.exceptions.ProxyError) or "proxy" in text.lower():
        from src.i18n import _

        return _("无法通过系统代理连接百度。请确认代理软件已启动；"
                 "或本次运行加 --no-proxy 直连。")
    return ""
