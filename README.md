# netdisk-stream-uploader

> **StreamPack** — A streaming netdisk uploader with volume-based compression.
>
> [中文文档](README.zh-CN.md)

Compress multiple files and folders into a ZIP archive, split it into volumes, and upload each volume to Baidu Netdisk as it is produced — without ever storing the whole archive in memory or on disk.

## Background

Uploading a large dataset to a netdisk usually follows this pattern:

1. Compress everything into a single large `.zip` file (taking gigabytes locally).
2. Upload it.
3. Delete the `.zip` after the upload completes.

This requires N GB of free local disk space, where N is the size of the compressed archive. For datasets on the order of 100 GB, most machines cannot provide that space on their system drive.

`netdisk-stream-uploader` uses a different approach:

1. **Streaming compression** — ZIP bytes are generated on the fly and never buffered as a whole.
2. **Volume splitting** — the byte stream is cut into volumes of a configurable size (1 GB by default).
3. **Upload-then-delete** — each volume is uploaded as soon as it is ready, then removed locally after a successful upload.
4. **Backpressure control** — the number of in-flight volumes is bounded, capping peak disk usage.
5. **Resumable** — task state is persisted in SQLite; an interrupted upload resumes from the last successful volume.

Peak local disk usage is approximately the size of a single volume (1 GB by default), independent of the total source size.

## Features

- Streaming compression, powered by [`stream-zip`](https://pypi.org/project/stream-zip/)
- Volume-based upload, each volume well under the Baidu API single-file limit
- Resumable uploads, with SQLite task tracking and automatic skipping of completed volumes
- Concurrent uploads, with a configurable worker count
- Backpressure control, an optional cap on in-flight volumes
- Resource-aware scheduling, choosing temp directories by free space and enforcing a minimum free-disk threshold
- Orphan cleanup, removing leftover temp files from crashed sessions on the next start
- Custom output extension, the archive is always ZIP but can be named `.zi123p` or similar
- OAuth 2.0 (Authorization Code, OOB), no cookie hacks and no password exposure

## Installation

```bash
pip install -r requirements.txt
```

Python 3.10 or newer is required.

## First run

```bash
python main.py upload \
  --source /path/to/files \
  --files "a.mp4,b.mp4" \
  --remote /apps/StreamPack \
  --name my_archive \
  --volume-size 1073741824
```

On the first run you will be prompted for your Baidu AppKey and SecretKey (from the [Baidu Netdisk Open Platform](https://pan.baidu.com/union)). They are stored in `~/.netdisk_uploader/credentials.json` and are not committed to version control.

You will then be asked to open an authorization URL in your browser and paste the resulting code back into the terminal. The token is stored in `~/.netdisk_uploader/token.json`.

## Usage

### Upload

```bash
# Upload specific files
python main.py upload -s /data -f "a.mp4,b.mp4" -r /apps/StreamPack -n backup

# Upload everything under --source (use ".")
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup

# Custom volume size (1 MB, useful for testing)
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup -v 1048576

# Enable backpressure (default cap = workers × 2)
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup --backpressure

# Custom backpressure cap
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup --backpressure=20

# Custom output extension
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup --ext .zi123p
```

### Resume an interrupted task

```bash
# List all tasks
python main.py list

# Show task details
python main.py status <task_id>

# Resume by ID
python main.py resume <task_id>

# Or: re-run the exact same command — task_id is deterministic
python main.py upload -s /data -f "a.mp4,b.mp4" -r /apps/StreamPack -n backup
```

### Delete task records

```bash
python main.py delete <task_id> --yes
```

## How it works

```
[source files] --stream--> [ZIP bytes] --split--> [volume 1] --upload--> [Baidu]
                                                   [volume 2] --upload--> [Baidu]
                                                   ...
                                                   [volume N] --upload--> [Baidu]
```

Each volume is deleted after a successful upload. Task state (which volumes succeeded) is stored in SQLite at `~/.netdisk_uploader/tasks.db`.

## Configuration

Currently edited in `src/config/settings.py`. Key options:

| Option                 | Default                                                 | Meaning                                   |
| :--------------------- | :------------------------------------------------------ | :---------------------------------------- |
| `preferred_temp_dirs`  | `D:/netdisk_temp`, `E:/netdisk_temp`, `C:/netdisk_temp` | Where volumes are stored during upload    |
| `min_free_disk_bytes`  | 2 GB                                                    | Refuse to use a disk with less free space |
| `max_parallel_workers` | 0 (= CPU cores − 1)                                     | Concurrent uploads                        |
| `max_pending_volumes`  | None                                                    | Backpressure cap (None = disabled)        |

A user config file is on the roadmap.

## Requirements

- Python 3.10+
- A Baidu Netdisk Open Platform application (free to register)

## Roadmap

- [ ] User config file and `config` command
- [ ] MD5 checksums for volumes, verified on resume
- [ ] Built-in `manifest.json` inside each volume
- [ ] Graphical interface (PySide6)
- [ ] Multi-language support (gettext)
- [ ] Additional netdisk providers (via a `StorageProvider` interface)
- [ ] Additional formats (RAR, 7z, encryption)

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Acknowledgements

- [`stream-zip`](https://pypi.org/project/stream-zip/) — streaming ZIP generation without buffering the whole archive
- [Baidu Netdisk Open Platform](https://pan.baidu.com/union)
