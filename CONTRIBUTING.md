# 参与贡献

欢迎 issue 和 pull request。这份文档说明怎么把环境跑起来、改动有哪些硬性约定，以及提 issue/PR 时最好带上什么。

## 环境

需要 **Python 3.10+**，Windows（打包与自签名只支持 Windows；核心逻辑跨平台）。

```bash
pip install -r requirements.txt          # 运行依赖
pip install -r requirements-dev.txt      # pytest
pip install -r requirements-gui.txt      # PySide6（只跑命令行可不装）
```

跑测试：

```bash
python -m pytest -q
```

没装 PySide6 时 GUI 测试会自动跳过（`importorskip`），其余用例照常跑。

直接从源码启动界面：

```bash
python main.py --gui
```

## 目录

| 路径 | 作用 |
|:---|:---|
| `src/core/` | 压缩分卷、上传调度、断点续传校验 |
| `src/storage/` | 存储后端接口与实现（加网盘只动这里，见 [docs/storage-provider.md](docs/storage-provider.md)） |
| `src/baidu/` | 百度 OAuth 与 HTTP 客户端 |
| `src/config/` | 配置、凭据、用户配置文件 |
| `src/database/` | SQLite 任务与分卷记录 |
| `src/gui/` | PySide6 界面 + 与 UI 无关的 viewmodel |
| `scripts/` | 构建、打包、清理、卸载、自签名脚本 |
| `packaging/` | PyInstaller spec 与安装器 |
| `locale/` | gettext 翻译目录 |
| `tests/` | 全部测试 |

## 硬性约定

这些不是风格偏好，是踩过坑的：

1. **版本号只改 `src/version.py`**，然后打同名 tag（`v0.9.0` ↔ `__version__ = "0.9.0"`）。CI 会校验，不一致直接失败。
2. **`.ps1` 必须带 UTF-8 BOM**。Windows PowerShell 5.1 会按 GBK 解析无 BOM 文件，中文注释会把引号吃掉，直接语法错误。CI 有检查。
3. **用户可见文案走 `_()` / `_f()`**，不要写裸 f-string（否则 msgid 每次不同，翻译表匹配不上）。改完跑：
   ```bash
   python scripts/extract_strings.py --missing   # 看漏了哪些
   python scripts/build_mo.py                    # 重新编译 .mo
   ```
4. **不要在 GUI 进程内给自己签名**。运行中的 exe 被锁定，必须交给 `trust-sign.ps1` 并以 PID 等待（见 `src/gui/qt_app.py` 的 `trust_and_sign`）。
5. **不要把凭据写进仓库**。`~/.netdisk_uploader/` 已被 `.gitignore` 排除，测试里也不要用真实 token。
6. **提交前跑一遍测试**，并在 PR 里贴 `python -m pytest -q` 的输出。

图标改了之后重新生成：

```bash
python scripts/make_icon.py    # 需要 Pillow；生成 assets/streampack.ico（7 种尺寸）
```

`tests/test_packaging.py` 会检查图标存在、规格文件引用它、以及 `.ps1` 的 BOM。

## 提 issue

- **缺陷**：复现步骤、`--debug` 日志片段、`python main.py --version` 的输出、Windows 版本
- **新网盘支持**：请用 [网盘支持模板](.github/ISSUE_TEMPLATE/provider-support.md)，重点是 API 文档、鉴权方式、单文件上限和错误码
- **界面/翻译**：截图 + 期望文案，中英文都可以提

安全问题请**不要**开公开 issue，直接邮件联系仓库所有者。

## 提 pull request

- 从 `main` 开分支，一个 PR 只做一件事
- 新功能带测试；改行为要同步 README（中英两份）
- 涉及界面的改动，附一张 offscreen 渲染的截图（`QT_QPA_PLATFORM=offscreen`，不会弹窗）
- 不加新依赖，除非在 PR 里说明理由

## 发布（维护者）

```powershell
# 1. 改 src/version.py 并提交
# 2. 打 tag 并推送
git tag -a v0.9.0 -m "v0.9.0: ..."
git push origin v0.9.0
```

推 tag 会触发 GitHub Actions：跑测试 → 校验版本与 BOM → 构建便携包与安装器 → 生成 `SHA256SUMS.txt` → 建 Release。

本地等效构建：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
```
