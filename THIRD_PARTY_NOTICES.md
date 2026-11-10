# 第三方组件声明

本项目以 Apache-2.0 发布（见 [LICENSE](LICENSE)）。发布的分发包（`StreamPack-*-win64.zip`、
`StreamPack-Setup-*.exe`）里**打包了**下列第三方组件的二进制，特此声明各自的许可证。

## 运行时依赖

| 组件 | 版本 | 许可证 | 用途 |
|:---|:---|:---|:---|
| [requests](https://github.com/psf/requests) | ≥2.31 | Apache-2.0 | HTTP 客户端 |
| [urllib3](https://github.com/urllib3/urllib3) | 2.x | MIT | requests 的传输层 |
| [certifi](https://github.com/certifi/python-certifi) | 2026.x | MPL-2.0 | CA 根证书包 |
| [charset-normalizer](https://github.com/Ousret/charset_normalizer) | 3.x | MIT | 字符集检测 |
| [idna](https://github.com/kjd/idna) | 3.x | BSD-3-Clause | 国际化域名 |
| [stream-zip](https://github.com/uktrade/stream-zip) | ≥0.0.83 | MIT | 流式 ZIP 生成（含 WinZip AES） |
| [pycryptodome](https://github.com/Legrandin/pycryptodome) | 3.x | BSD-2-Clause / Public Domain | AES 实现 |
| [click](https://github.com/pallets/click) | ≥8.1 | BSD-3-Clause | 命令行框架 |
| [psutil](https://github.com/giampaolo/psutil) | ≥5.9 | BSD-3-Clause | 系统信息（磁盘/内存） |
| [PySide6-Essentials](https://www.qt.io/qt-for-python) / [shiboken6](https://pypi.org/project/shiboken6/) | ≥6.6 | **LGPL-3.0**（或 GPL-2.0/GPL-3.0） | 图形界面（Qt） |

## 关于 PySide6 / Qt（LGPL-3.0）

分发包里带了 Qt 的动态库。LGPL-3.0 的要求是：

1. 声明使用了 Qt，并附上许可证文本（见下方链接）；
2. 允许用户**替换**这些库 —— 本项目的分发包是普通目录（不是压缩的可执行镜像），
   `_internal/` 下的 Qt DLL 可以直接替换成你自己构建的版本，无需重新打包；
3. 如果你修改了 Qt 本身并分发，需要公开修改后的源码。

许可证全文：

- LGPL-3.0：<https://www.gnu.org/licenses/lgpl-3.0.html>
- Qt 的授权说明：<https://www.qt.io/licensing/open-source-lgpl-obligations>
- PySide6 源码：<https://code.qt.io/cgit/pyside/pyside-setup.git/>

## 不打包二进制时

从源码运行（`pip install -r requirements.txt`）只依赖上面这些包，各自的许可证由 pip 安装在
`site-packages` 的元数据里，本项目不再重复分发。

## 开发期依赖（不进入分发包）

pytest（MIT）、PyInstaller（GPL-2.0 + 例外条款，仅用于构建）。
