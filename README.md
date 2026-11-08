# netdisk-stream-uploader

> **StreamPack** — A streaming netdisk uploader with volume-based compression.
>
> [中文文档](README.zh.md)

`netdisk-stream-uploader` is the project name (repository / PyPI package); `StreamPack` is the product name — and the application name registered on the Baidu Open Platform, which is why the upload path is fixed to `/apps/StreamPack`.

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
- Source fingerprint check, confirming nothing changed before a resume
- Self-describing archive: the first ZIP entry is `__streampack__.json` (task parameters + full file list)
- Automatic retries with exponential backoff (3 attempts by default)
- Concurrent uploads, with a configurable worker count
- Backpressure control, an optional cap on in-flight volumes
- Resource-aware scheduling, choosing temp directories by free space and enforcing a minimum free-disk threshold
- Orphan cleanup, removing leftover temp files from crashed sessions on the next start
- Custom output extension, the archive is always ZIP but can be named `.zi123p` or similar
- Optional AES-256 encryption (WinZip AES); the password is never stored, not even in the archive metadata
- OAuth 2.0 (Authorization Code, OOB), no cookie hacks and no password exposure

## Installation

```bash
pip install -r requirements.txt
```

Python 3.10 or newer is required.

## Why you need your own app credentials

This is a **local client that runs no server of its own**: files go straight from your machine to your netdisk, with no third party in between. Two consequences follow:

- **Nobody can see your data** — no relay, no logs, no backend;
- **The author cannot hold credentials for you** — that would mean operating your account under someone else's identity, with every upload counted against their quota, their app review, and their record of violations.

So each user registers their own app once. It is free; the only cost is a one-time identity verification (about 10 minutes).

> Do not use an AppKey / SecretKey handed to you by someone else. It puts your files under their app quota, and lets them switch it off whenever they like.

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

# Encrypted archive (prefer the environment variable over the command line)
export STREAMPACK_PASSWORD='your-pass'
python main.py upload -s /data -f "." -r /apps/StreamPack -n backup --password "$STREAMPACK_PASSWORD"
```

### Offline rehearsal

Switch the backend to `local` and volumes are written to a local directory instead of the netdisk — no network, no quota, so you can check the split before committing to an upload.

```bash
python main.py upload -s /data -f "." -r /backup -n backup -v 1048576 --provider local
```

Output lands in `./_rehearsal/backup/` (change it with `config set local_rehearsal_dir`). The same works for rehearsing a resume offline: Ctrl+C halfway, then `resume <task_id> --provider local`.

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

If the process is killed (window closed, power loss), the task stays in `running`. The next command of any kind resets it to `interrupted` and prints how many tasks were reset; `resume` continues from there.

Before resuming, the source files are fingerprinted. If anything was added, removed or modified, the differences are listed and the resume aborts rather than continuing with a misaligned stream:

```bash
python main.py resume <task_id>                 # default full: fingerprint + per-volume MD5
python main.py resume <task_id> --verify=stat   # fingerprint only
python main.py resume <task_id> --verify=off    # skip the check (only if you know nothing changed)
```

### Graphical interface

```bash
pip install -r requirements-gui.txt   # PySide6-Essentials only, no QtWebEngine
python main.py --gui
```

The window shows the task list with progress, per-volume details, resume, delete, and a live log pane. The resume button runs `python main.py resume <id>` under the hood — the exact same logic as the CLI.

### Delete task records

```bash
python main.py delete <task_id> --yes
```

Only local records are deleted; uploaded files are untouched.

### Global options

These apply to every subcommand and go before it:

```bash
python main.py --debug list              # DEBUG-level logging
python main.py --quiet list              # WARNING and above only
python main.py --log-file run.log list   # also write to a log file
python main.py --lang en list            # switch language (zh / en / auto)
python main.py --version                 # print the version
python main.py --gui                     # graphical interface
```

`--debug` takes precedence over `--quiet`.

## How it works

```
[source files] --stream--> [ZIP bytes] --split--> [volume 1] --upload--> [Baidu]
                                                   [volume 2] --upload--> [Baidu]
                                                   ...
                                                   [volume N] --upload--> [Baidu]
```

Each volume is deleted after a successful upload. Task state (which volumes succeeded) is stored in SQLite at `~/.netdisk_uploader/tasks.db`.

A failed volume is retried with exponential backoff: 3 attempts by default, waiting 2s then 4s. Only after all attempts fail is the volume marked `failed`; the remaining volumes continue normally.

## Configuration

Configuration resolves in this order (highest first):

1. environment variables `STREAMPACK_<FIELD NAME>`
2. the user config file `~/.netdisk_uploader/config.json`
3. code defaults in `src/config/settings.py`

Manage it with the `config` command — no source edits needed:

```bash
python main.py config list                      # every option and where its value comes from
python main.py config get max_upload_retries    # read one
python main.py config set max_upload_retries 5  # change one (written to the config file)
python main.py config unset max_upload_retries  # back to the default
python main.py config path                      # where the config file lives
```

The file only stores options you changed explicitly; everything else keeps following the code defaults, so new defaults take effect after an upgrade.

Options:

| Option | Default | Meaning |
|:---|:---|:---|
| `preferred_temp_dirs` | `D:/netdisk_temp`, `E:/netdisk_temp`, `C:/netdisk_temp` | Where volumes are stored during upload |
| `min_free_disk_bytes` | 2 GB | Refuse to use a disk with less free space |
| `max_parallel_workers` | 0 (= CPU cores − 1) | Concurrent uploads |
| `max_pending_volumes` | None | Backpressure cap (None = disabled; the CLI flag wins) |
| `max_upload_retries` | 3 | Max attempts per volume, including the first |
| `retry_backoff_seconds` | 2.0 | Delay before the first retry; doubles each time |
| `provider` | `baidu` | Storage backend: `baidu` or `local` |
| `local_rehearsal_dir` | `./_rehearsal` | Root directory for `provider=local` |
| `remote_root` | `/apps/StreamPack` | Default upload directory — **change it to your own app name** |

Any option can also be overridden per-session with an environment variable:

```bash
export STREAMPACK_MAX_UPLOAD_RETRIES=1     # Linux / macOS
set STREAMPACK_MAX_UPLOAD_RETRIES=1        # Windows
```

## Language

Both the command line and the GUI ship in Chinese and English. Three ways to switch:

```bash
python main.py --lang en --gui          # one run
python main.py config set language en   # persist it
export STREAMPACK_LANG=en               # environment variable
```

Priority: `--lang` > `STREAMPACK_LANG` > the `language` config option > system locale. `language` defaults to `auto`.

The source strings are the Chinese text in the code; the English catalogue lives in `locale/en/LC_MESSAGES/streampack.po`. After editing it:

```bash
python scripts/build_mo.py     # .po → .mo, pure stdlib, no gettext toolchain needed
```

Adding a language: copy `locale/en` to `locale/<lang>`, translate the `.po`, rebuild, then add the code to `SUPPORTED` in `src/i18n.py`.

## Packaging

```powershell
pip install pyinstaller
pwsh -File scripts/build_release.ps1
```

Output:

| Path | Purpose |
|:---|:---|
| `dist/StreamPack/` | Runnable as-is, no installation |
| `dist/StreamPack-<version>-win64.zip` | Portable package, unzip and run |
| `dist/StreamPack-Setup-<version>.exe` | Installer, the recommended option for end users |

The folder holds two entry points: `streampack.exe` (command line, keeps the console so logs are visible) and `streampack-gui.exe` (GUI, no console window). Both share the dependencies in `_internal/`.

**Ship only the installer and the portable package** — a bare exe cannot run without its `_internal/` folder.

Pushing a `v*` tag makes GitHub Actions run the tests, build both artifacts and create a release (`.github/workflows/build.yml`) — nothing needs to be installed locally.

### Installing

Double-click `StreamPack-Setup-<version>.exe`. It installs into `%LOCALAPPDATA%\Programs\StreamPack` — **no administrator rights needed** — and creates a Start Menu shortcut plus an entry under Settings → Apps.

Silent install (for scripts):

```powershell
.\StreamPack-Setup-0.7.0.exe --silent --result-file install.json
# {"ok": true, "version": "0.7.0", "install_dir": "...", "copied": 7}
```

Options: `--dir <path>` to change the location, `--no-shortcut` to skip the shortcut. On failure the exit code is 1 and the result file carries an `error` field.

### Running the download

1. **Extract it first** — double-clicking an exe inside the archive will not find the sibling `_internal` folder
2. `streampack-gui.exe` opens the GUI; `streampack.exe` is the command line
3. On first run, enter your Baidu AppKey / SecretKey, then authorize in the browser and paste the code

**About "Unknown publisher"**: the build is not code-signed, so SmartScreen warns once — click "More info → Run anyway".

#### Local self-signing (optional)

If you only use it on your own machine, signing once makes the warning go away. After installing, open **Tools → Trust and sign locally (this machine only)…**. The confirmation lists exactly what will happen:

- create or reuse a local root `CN=Hungry-M` for the current Windows user
- issue a project child certificate `CN=StreamPack`
- add the root to "Trusted Root Certification Authorities" and the child to "Trusted Publishers"
- sign the program, then reopen it

A running exe cannot sign itself (the file is locked), so the app hands its PID and path to `trust-sign.ps1` and exits; the helper waits for the file to unlock, signs, and relaunches.

**This is local-only**: distributing the signed build does not help anyone else — their machines do not trust your certificate and will report an invalid one. To verify a download instead, compare with `SHA256SUMS.txt` in the release:

```powershell
Get-FileHash .\StreamPack-v0.9.0-win64.zip -Algorithm SHA256
```

To remove the trust again:

```powershell
powershell -ExecutionPolicy Bypass -File trust-sign.ps1 -RemoveCert
```

It removes the child first and only deletes the root when no other project's child certificate is left.

If you would rather not run an exe at all, the source works the same way:

```bash
pip install -r requirements.txt -r requirements-gui.txt
python main.py --gui
```

### Uninstalling

**Installed version**: Settings → Apps → StreamPack, or run `uninstall.cmd` in the install directory. Credentials and the task database are kept by default; add `-PurgeData` to remove them too.

**Portable version**: no installer, so no uninstaller. It only leaves things in two places:

1. Delete the extracted `StreamPack` folder
2. Delete `%USERPROFILE%\.netdisk_uploader\` — credentials, token, task database and config all live there

The folder ships with `cleanup.cmd`: double-clicking it only **previews** what would be removed; pass a flag to act:

```cmd
cleanup.cmd -Yes
```

It only deletes files it can prove are ours (by checking for `credentials.json` / `token.json` / `tasks.db` / `config.json`), never scans by folder name, never kills processes, and never touches certificates — those need `trust-sign.ps1 -RemoveCert`.

Doing it by hand is just as fine:

```powershell
Remove-Item "$env:USERPROFILE\.netdisk_uploader" -Recurse -Force
```

Files already uploaded to the netdisk are **not** deleted; remove those in the netdisk itself.

## FAQ

**`invalid_client` during authorization**

Almost always a mangled SecretKey. The common cause is an invisible control character pasted in from the web console (e.g. `\u0016`), or a stray space or newline. Copy it again from the console; the stored string is visible in `token.json`.

**Upload returns 404 or 403**

The target directory does not exist or is not writable. The current console no longer has a separate "app directory" field: writable paths are fixed to `/apps/{app name}`, and the **app name must be ASCII** — a Chinese name breaks the path.

If you are using someone else's open-source tool with your own app, the default `/apps/StreamPack` is the author's app name and will not match yours. Set it once:

```bash
python main.py config set remote_root /apps/your-app-name
```

**`invalid_grant` when exchanging the code**

Authorization codes are single-use and expire in about 10 minutes. Reopen the authorization URL and get a fresh one.

**Do I need to re-authorize often**

Normally no. The access token expires in about 30 days and is refreshed automatically with the refresh token; you are only asked to authorize again when the refresh itself fails.

**How much local disk do I need**

Roughly `volume size × (1 + in-flight volumes)`. Backpressure is off by default, so the peak can approach the whole archive; use `--backpressure` to keep it tight.

**Can I interrupt it**

Yes. Ctrl+C stops scheduling further volumes and removes the ones not yet uploaded; completed volumes stay recorded, and `resume` continues from there.

**How do I put the volumes back together**

Each volume is a slice of one continuous ZIP byte stream — concatenate them in order:

```bash
cat arch.part*.zip > arch.zip
```

```cmd
copy /b arch.part0001.zip+arch.part0002.zip+arch.part0003.zip arch.zip
```

The result opens directly. Its first entry is `__streampack__.json`, recording the task parameters and the full file list, so you can tell where the volumes came from.

**How do I open an encrypted archive**

Use a tool that supports WinZip AES — 7-Zip, WinRAR, Keka. **Windows Explorer's built-in ZIP support does not handle AES**; a "cannot open" error there does not mean the archive is corrupt.

The password is stored nowhere (the database only keeps a PBKDF2 verifier for comparison), so losing it is unrecoverable. To resume an encrypted task you must supply the same password — `--password` or `STREAMPACK_PASSWORD`. Without it you are prompted; with a wrong one the resume is rejected immediately instead of wasting a full compression pass.

## Requirements

- Python 3.10+
- A Baidu Netdisk Open Platform application (free to register)

## Roadmap

- [x] User config file and `config` command
- [x] MD5 checksums for volumes, verified per volume on resume
- [x] `__streampack__.json` manifest embedded in the archive
- [x] AES-256 encrypted archives
- [x] `StorageProvider` interface, with a local-directory implementation
- [x] Graphical interface (PySide6, basic: task list / resume / volume details / log)
- [x] Multi-language (gettext): both CLI and GUI
- [ ] More language catalogues (only `en` so far)
- [ ] A second real netdisk backend (interface ready, no account to verify against)
- [ ] 7z — needs a randomly-addressable container, which conflicts with streaming; deferred

> RAR is out of scope: the compression algorithm is proprietary, the vendor only open-sources extraction and forbids using it to create archives, and no Python writer exists.

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Acknowledgements

- [`stream-zip`](https://pypi.org/project/stream-zip/) — streaming ZIP generation without buffering the whole archive
- [Baidu Netdisk Open Platform](https://pan.baidu.com/union)
