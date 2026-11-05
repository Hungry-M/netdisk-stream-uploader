# netdisk-stream-uploader

> **StreamPack** — 分卷流式网盘上传工具
>
> [English](README.md)

`netdisk-stream-uploader` 是项目名（仓库名 / PyPI 包名），`StreamPack` 是产品名，同时也是百度开放平台里的应用名 —— 上传路径因此固定为 `/apps/StreamPack`。

将多个文件或文件夹压缩为一个 ZIP 分卷包，边压缩边上传至百度网盘，全程无需在本地保存完整压缩包。

## 背景

把大量数据上传到网盘，传统流程如下：

1. 把全部内容压缩成一个完整的 `.zip` 文件（本地占用数 GB）。
2. 上传。
3. 上传完成后删除 `.zip`。

这一流程要求本地有 N GB 的空闲磁盘空间（N 为压缩包大小）。对于 100 GB 量级的数据集，多数机器的系统盘无法提供这么多空间。

`netdisk-stream-uploader` 采用不同的流程：

1. **流式压缩**：实时生成 ZIP 字节流，不将整个压缩包缓存在内存或磁盘。
2. **分卷切分**：将字节流按可配置大小（默认 1 GB）切分为多个分卷。
3. **上传后删除**：每个分卷生成完毕即上传，成功后删除本地文件。
4. **背压控制**：限制在途分卷数量，控制峰值磁盘占用。
5. **断点续传**：任务状态持久化到 SQLite；中断后从上次成功的分卷继续。

在整个过程中，本地峰值磁盘占用约等于一个分卷的大小（默认 1 GB），与源数据总大小无关。

## 特性

- 流式压缩，基于 [`stream-zip`](https://pypi.org/project/stream-zip/)
- 分卷上传，每个分卷都远小于百度 API 的单文件上限
- 断点续传，SQLite 任务追踪，已完成分卷自动跳过
- 源文件指纹校验，续传前确认源文件未被改动
- 归档自描述，ZIP 第一个条目是 `__streampack__.json`（任务参数 + 完整文件清单）
- 失败重试，按指数退避自动重试（默认 3 次）
- 并发上传，工作线程数可配置
- 背压控制，可选，限制同时在传的分卷数量
- 资源感知调度，按剩余空间选择临时目录，并强制最低磁盘空间下限
- 孤儿分卷清理，异常退出后遗留的临时文件下次启动时自动清理
- 自定义扩展名，内容始终是 ZIP，但可以命名为 `.zi123p` 等
- 可选 AES-256 加密（WinZip AES），密码不落盘、不进归档元数据
- OAuth 2.0 授权码模式（OOB），不依赖 Cookie，不暴露密码

## 安装

```bash
pip install -r requirements.txt
```

需要 Python 3.10 或更新版本。

## 首次运行

```bash
python main.py upload \
  --source /path/to/files \
  --files "a.mp4,b.mp4" \
  --remote /apps/StreamPack \
  --name my_archive \
  --volume-size 1073741824
```

首次运行会要求输入百度 AppKey 与 SecretKey（从[百度网盘开放平台](https://pan.baidu.com/union)申请获取）。它们保存在 `~/.netdisk_uploader/credentials.json`，不会进入版本控制。

随后程序会要求你在浏览器打开授权链接，完成后将返回的授权码粘贴回终端。令牌保存在 `~/.netdisk_uploader/token.json`。

## 用法

### 上传

```bash
# 上传指定的几个文件
python main.py upload -s /data -f "a.mp4,b.mp4" -r /apps/StreamPack -n backup

# 上传 source 目录下的全部内容（用 "."）
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup

# 自定义分卷大小（1 MB，便于测试）
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup -v 1048576

# 启用背压（默认上限 = 工作线程数 × 2）
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup --backpressure

# 自定义背压上限
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup --backpressure=20

# 自定义输出扩展名
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup --ext .zi123p

# 加密归档（建议用环境变量传密码，避免留在命令历史里）
export STREAMPACK_PASSWORD='your-pass'
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup --password "$STREAMPACK_PASSWORD"
```

### 离线预演

把后端换成 `local`，分卷写到本地目录而不是网盘：不联网、不占配额，可以先确认切分是否符合预期。

```bash
python main.py upload -s D:\data -f "." -r /backup -n backup -v 1048576 --provider local
```

结果落在 `./_rehearsal/backup/` 下（位置可用 `config set local_rehearsal_dir` 改）。同样的方式也能在断网时演练续传：跑到一半按 Ctrl+C，再 `resume <task_id> --provider local`。

### 断点续传

```bash
# 查看任务列表
python main.py list

# 查看任务详情
python main.py status <task_id>

# 按 ID 续传
python main.py resume <task_id>

# 或用完全相同的参数重跑 —— task_id 是确定性的
python main.py upload -s /data -f "a.mp4,b.mp4" -r /apps/StreamPack -n backup
```

进程被强制结束（关窗口、断电）时，任务状态会停在 `running`。下次启动任意命令时会自动把它复位成 `interrupted`，并用一行提示告诉你数量，之后 `resume` 即可继续。

续传前会比对源文件指纹。若文件被增删改，程序会列出具体差异并中止，不会用错位的数据继续上传：

```bash
python main.py resume <task_id>                 # 默认 full：指纹 + 逐卷 MD5
python main.py resume <task_id> --verify=stat   # 只比指纹
python main.py resume <task_id> --verify=off    # 跳过校验（确定源没变时用）
```

### 图形界面

```bash
pip install -r requirements-gui.txt   # 只装 PySide6-Essentials，不含 QtWebEngine
python main.py --gui
```

窗口里能看任务列表与进度、查看分卷明细、续传、删除记录，底部是实时日志。续传按钮背后调的是 `python main.py resume <id>`，与命令行完全同一套逻辑。

### 删除任务记录

```bash
python main.py delete <task_id> --yes
```

只删本地记录，不影响已上传到网盘的文件。

### 全局选项

以下开关对所有子命令生效，写在子命令之前：

```bash
python main.py --debug list              # 输出 DEBUG 级日志
python main.py --quiet list              # 只输出 WARNING 及以上
python main.py --log-file run.log list   # 同时写入日志文件
python main.py --gui                     # 图形界面（尚未实现）
```

`--debug` 优先于 `--quiet`。

## 原理

```
[源文件] --流式压缩--> [ZIP 字节流] --切分--> [分卷 1] --上传--> [百度网盘]
                                              [分卷 2] --上传--> [百度网盘]
                                              ...
                                              [分卷 N] --上传--> [百度网盘]
```

每个分卷上传成功后立即删除本地文件。任务状态（哪些分卷已成功）保存在 `~/.netdisk_uploader/tasks.db`。

单个分卷上传失败时按指数退避重试：默认最多尝试 3 次，间隔 2s、4s。全部失败才会把该分卷标记为 `failed`，其余分卷照常继续。

## 配置

配置按优先级生效（高 → 低）：

1. 环境变量 `STREAMPACK_<字段名大写>`
2. 用户配置文件 `~/.netdisk_uploader/config.json`
3. `src/config/settings.py` 里的代码默认值

用 `config` 命令管理，不需要改源码：

```bash
python main.py config list                      # 列出全部配置项及其来源（env / file / default）
python main.py config get max_upload_retries    # 读一项
python main.py config set max_upload_retries 5  # 改一项，写入配置文件
python main.py config unset max_upload_retries  # 恢复默认值
python main.py config path                      # 显示配置文件位置
```

配置文件只保存你显式改过的项，其余字段继续跟随代码默认值——这样升级后新的默认值能自动生效。

可配置项：

| 配置项 | 默认值 | 含义 |
|:---|:---|:---|
| `preferred_temp_dirs` | `D:/netdisk_temp`, `E:/netdisk_temp`, `C:/netdisk_temp` | 分卷临时存储位置 |
| `min_free_disk_bytes` | 2 GB | 低于此剩余空间的磁盘不使用 |
| `max_parallel_workers` | 0（= CPU 核数 − 1） | 并发上传数 |
| `max_pending_volumes` | None | 背压上限（None = 不启用；命令行参数优先） |
| `max_upload_retries` | 3 | 单个分卷的最大尝试次数（含首次） |
| `retry_backoff_seconds` | 2.0 | 首次重试的等待秒数，之后按 2 的幂递增 |
| `provider` | `baidu` | 存储后端：`baidu` 或 `local` |
| `local_rehearsal_dir` | `./_rehearsal` | `provider=local` 时的落盘根目录 |

也可以临时用环境变量覆盖，不改文件：

```bash
set STREAMPACK_MAX_UPLOAD_RETRIES=1        # Windows
export STREAMPACK_MAX_UPLOAD_RETRIES=1     # Linux / macOS
```

## 多语言

界面提供中英文，三种切换方式：

```bash
python main.py --lang en --gui          # 单次
python main.py config set language en   # 持久（写进配置文件）
set STREAMPACK_LANG=en                  # 环境变量（Windows，临时）
```

优先级：`--lang` > `STREAMPACK_LANG` > 配置项 `language` > 系统区域。`language` 默认 `auto`，按系统区域判断。

翻译的源字符串就是代码里的中文原文，英文目录在 `locale/en/LC_MESSAGES/streampack.po`。改完执行：

```bash
python scripts/build_mo.py     # .po → .mo，纯标准库实现，不需要 gettext 工具链
```

新增语言：把 `locale/en` 整个复制成 `locale/<lang>`，翻译 `.po` 后重新编译，再把语言代码加进 `src/i18n.py` 的 `SUPPORTED`。

## 常见问题

**授权时报 `invalid_client`**

多半是 SecretKey 复制出错。最常见的是粘贴时混入了不可见控制字符（例如 `\u0016`，通常出现在从网页复制时），也可能是首尾多了空格或换行。重新从控制台复制一次即可；`token.json` 里能直接看到存进去的字符串。

**上传返回 404 或 403**

目标目录不存在或没有权限。新版开放平台不再提供单独的「应用目录」字段，可写目录固定为 `/apps/{应用名称}`，且**应用名称必须是英文**——中文名会导致路径异常。先用 `--remote /apps/<你的应用名>` 确认，再考虑改默认值。

**授权码无效（`invalid_grant`）**

授权码一次性使用，且有效期约 10 分钟。过期就重新打开授权链接重新获取。

**要重新授权吗**

一般不用。access_token 约 30 天过期，程序会自动用 refresh_token 换新；只有刷新也失败时才会提示重新授权。

**本地至少要留多少空间**

约等于 `分卷大小 × (1 + 在途分卷数)`。默认不启用背压，峰值可能接近整个压缩包；想压到最小就用 `--backpressure`。

**能中断吗**

可以。Ctrl+C 会停止后续分卷并清理未上传的临时文件，已上传的分卷记录保留在数据库里，下次 `resume` 从断点继续。

**怎么把分卷还原成一个 zip**

分卷是整条 ZIP 字节流的切片，按编号顺序拼接即可：

```cmd
copy /b arch.part0001.zip+arch.part0002.zip+arch.part0003.zip arch.zip
```

```bash
cat arch.part*.zip > arch.zip
```

还原后直接解压即可。第一个条目是 `__streampack__.json`，记录了任务参数和完整文件清单，可以据此确认这批分卷是从哪来的。

**加密归档怎么打开**

用支持 WinZip AES 的工具：7-Zip、WinRAR、Keka 都可以。**Windows 资源管理器自带的 zip 不支持 AES**，双击会报错，不是文件坏了。

密码不会保存在任何地方（数据库里只有用于比对的 PBKDF2 校验值），丢了无法恢复。续传加密任务时必须提供同一个密码：`--password` 或环境变量 `STREAMPACK_PASSWORD`；没给会交互提示，密码不对会直接拒绝而不是白跑一遍。

## 环境要求

- Python 3.10+
- 百度网盘开放平台应用（免费申请）

## 路线图

- [x] 用户配置文件与 `config` 命令
- [x] 分卷 MD5 落库，续传时逐卷校验
- [x] 归档内置 `__streampack__.json` 清单
- [x] AES-256 加密归档
- [x] 存储后端接口（`StorageProvider`），附本地目录实现
- [x] 图形界面（PySide6，基础版：任务列表 / 续传 / 分卷明细 / 日志）
- [x] 多语言（gettext）：界面中英文可用
- [ ] CLI 提示语抽取到 gettext
- [ ] 第二个真实网盘后端（接口已就位，缺账号验证）
- [ ] 7z —— 需要能随机访问的容器，与流式生成冲突，暂缓

> RAR 不在计划内：压缩算法专有，官方只开源解压、禁止用于生成，Python 侧没有可用的写入实现。

## 许可证

Apache License 2.0 — 见 [LICENSE](LICENSE)。

## 致谢

- [`stream-zip`](https://pypi.org/project/stream-zip/) — 流式 ZIP 生成，无需缓存整个压缩包
- [百度网盘开放平台](https://pan.baidu.com/union)
