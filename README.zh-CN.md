- # netdisk-stream-uploader

  > **StreamPack** — 分卷流式网盘上传工具
  >
  > [English](README.md)

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
  - 并发上传，工作线程数可配置
  - 背压控制，可选，限制同时在传的分卷数量
  - 资源感知调度，按剩余空间选择临时目录，并强制最低磁盘空间下限
  - 孤儿分卷清理，异常退出后遗留的临时文件下次启动时自动清理
  - 自定义扩展名，内容始终是 ZIP，但可以命名为 `.zi123p` 等
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
  ```

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

  ### 删除任务记录

  ```bash
  python main.py delete <task_id> --yes
  ```

  ## 原理

  ```
  [源文件] --流式压缩--> [ZIP 字节流] --切分--> [分卷 1] --上传--> [百度网盘]
                                                [分卷 2] --上传--> [百度网盘]
                                                ...
                                                [分卷 N] --上传--> [百度网盘]
  ```

  每个分卷上传成功后立即删除本地文件。任务状态（哪些分卷已成功）保存在 `~/.netdisk_uploader/tasks.db`。

  ## 配置

  当前需要编辑 `src/config/settings.py`。关键项：

  | 配置项                 | 默认值                                                  | 含义                       |
  | :--------------------- | :------------------------------------------------------ | :------------------------- |
  | `preferred_temp_dirs`  | `D:/netdisk_temp`, `E:/netdisk_temp`, `C:/netdisk_temp` | 分卷临时存储位置           |
  | `min_free_disk_bytes`  | 2 GB                                                    | 低于此剩余空间的磁盘不使用 |
  | `max_parallel_workers` | 0（= CPU 核数 − 1）                                     | 并发上传数                 |
  | `max_pending_volumes`  | None                                                    | 背压上限（None = 不启用）  |

  持久化的用户配置文件在路线图中。

  ## 环境要求

  - Python 3.10+
  - 百度网盘开放平台应用（免费申请）

  ## 路线图

  - [ ] 用户配置文件与 `config` 命令
  - [ ] 分卷 MD5 校验，续传时验证
  - [ ] 每个分卷内置 `manifest.json`
  - [ ] 图形界面（PySide6）
  - [ ] 多语言支持（gettext）
  - [ ] 更多网盘后端（通过 `StorageProvider` 接口）
  - [ ] 更多格式（RAR、7z、加密）

  ## 许可证

  Apache License 2.0 — 见 [LICENSE](LICENSE)。

  ## 致谢

  - [`stream-zip`](https://pypi.org/project/stream-zip/) — 流式 ZIP 生成，无需缓存整个压缩包
  - [百度网盘开放平台](https://pan.baidu.com/union)
