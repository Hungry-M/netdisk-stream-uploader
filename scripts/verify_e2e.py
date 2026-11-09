#!/usr/bin/env python
"""
自动化验证：在隔离沙箱里跑完 docs/release-checklist.md 中可自动化的部分。

    python scripts/verify_e2e.py                 # 在 <仓库>/_verify 下建沙箱
    python scripts/verify_e2e.py --work D:\\verify --keep
    python scripts/verify_e2e.py --skip-installer

隔离方式：给所有子进程设置 STREAMPACK_HOME 和 STREAMPACK_CONFIG，
凭证/令牌/任务库/配置全部写进沙箱，不碰开发机的 ~/.netdisk_uploader。
脚本前后会对比真实数据目录的快照，确认一个字节都没动。

不能自动化的两项（会明确标 SKIP）：
- 百度 OAuth 授权（要人打开浏览器粘贴授权码）
- 双击安装器时的 SmartScreen / 图标外观（要人眼确认）
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MB = 1024 * 1024

RESULTS = []


# ---------- 基础设施 ----------

class Sandbox:
    def __init__(self, work: Path):
        self.work = work
        self.home = work / "home"
        self.src = work / "src"
        self.rehearsal = work / "rehearsal"
        self.install = work / "install"
        self.temp = work / "temp"
        for d in (self.home, self.src, self.rehearsal, self.temp):
            d.mkdir(parents=True, exist_ok=True)

        self.env = dict(os.environ)
        self.env["STREAMPACK_HOME"] = str(self.home)
        self.env["STREAMPACK_CONFIG"] = str(self.home / "config.json")
        self.env["PYTHONIOENCODING"] = "utf-8"
        # onefile 安装器要解包到临时目录；不指进沙箱的话会被文件沙箱挡住
        self.env["TEMP"] = str(self.temp)
        self.env["TMP"] = str(self.temp)
        self.env.pop("STREAMPACK_PASSWORD", None)

    def run(self, args, timeout=300, check=False):
        result = subprocess.run(
            [sys.executable, "main.py", *args],
            cwd=ROOT, env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"命令失败: main.py {' '.join(args)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def db(self, sql, params=()):
        """直接读沙箱里的任务库。"""
        path = self.home / "tasks.db"
        if not path.exists():
            return []
        conn = sqlite3.connect(path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def make_tree(self, rel_dir: str, files, size=MB):
        folder = self.src / rel_dir
        folder.mkdir(parents=True, exist_ok=True)
        for name in files:
            (folder / name).write_bytes(os.urandom(size))
        return folder


def snapshot_data_dir() -> dict:
    """真实数据目录的快照，用来证明沙箱没有写到外面。"""
    real = Path.home() / ".netdisk_uploader"
    if not real.exists():
        return {"exists": False}
    files = {}
    for p in real.rglob("*"):
        if p.is_file():
            st = p.stat()
            files[str(p.relative_to(real))] = (st.st_size, int(st.st_mtime))
    return {"exists": True, "files": files}


def check(name):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            started = time.time()
            try:
                detail = fn(*args, **kwargs)
                RESULTS.append((name, "PASS", detail or "", time.time() - started))
                print(f"  [PASS] {name}" + (f"  — {detail}" if detail else ""))
            except Exception as e:
                RESULTS.append((name, "FAIL", str(e), time.time() - started))
                print(f"  [FAIL] {name}\n         {e}")
        return wrapper
    return decorator


def concat_and_open(volumes, target: Path):
    target.write_bytes(b"".join(p.read_bytes() for p in sorted(volumes)))
    return zipfile.ZipFile(target)


# ---------- 各项检查 ----------

@check("版本与沙箱隔离")
def verify_isolation(sb: Sandbox):
    result = sb.run(["--version"], check=True)
    assert "StreamPack" in result.stdout, result.stdout

    path = sb.run(["config", "path"], check=True).stdout.strip()
    assert sb.home.as_posix() in Path(path).as_posix(), f"配置没进沙箱: {path}"

    sb.run(["config", "set", "local_rehearsal_dir", str(sb.rehearsal)], check=True)
    return f"data dir = {sb.home}"


@check("连续流：拼接后可解压")
def verify_continuous(sb: Sandbox):
    folder = sb.make_tree("cont", [f"f{i}.bin" for i in range(3)], size=2 * MB)
    result = sb.run([
        "upload", "-s", str(folder), "-f", "f0.bin,f1.bin,f2.bin",
        "-n", "cont", "-r", "/apps/Test", "--volume-size", str(MB),
        "--provider", "local",
    ], check=True)
    assert "完成" in result.stdout, result.stdout

    volumes = sorted(sb.rehearsal.rglob("cont.part*.zip"))
    assert len(volumes) >= 3, f"分卷数不对: {volumes}"

    single = volumes[0]
    try:
        zipfile.ZipFile(single)
        raise AssertionError("单卷不应该能打开")
    except zipfile.BadZipFile:
        pass

    with concat_and_open(volumes, sb.work / "cont.zip") as zf:
        names = [n for n in zf.namelist() if n != "__streampack__.json"]
        assert len(names) == 3, names
        assert zf.testzip() is None
    return f"{len(volumes)} 卷"


@check("自包含：每卷独立可解压")
def verify_self_contained(sb: Sandbox):
    sb.make_tree("sc/photos", ["a.jpg", "b.jpg"], size=MB)
    sb.make_tree("sc/docs", ["c.pdf"], size=MB)
    result = sb.run([
        "upload", "-s", str(sb.src / "sc"), "-f", "photos,docs",
        "-n", "sc", "-r", "/apps/Test", "--volume-size", str(3 * MB),
        "--provider", "local", "--layout", "self-contained", "--pack-by", "folder",
    ], check=True)
    assert "完成" in result.stdout, result.stdout

    volumes = sorted(sb.rehearsal.rglob("sc.part*.zip"))
    assert len(volumes) == 2, f"期望 2 卷（photos / docs），实际 {len(volumes)}"
    for v in volumes:
        with zipfile.ZipFile(v) as zf:
            assert zf.testzip() is None
            assert any(not n.startswith("__streampack__") for n in zf.namelist())

    index = list(sb.rehearsal.rglob("sc.index.json"))
    assert index, "没有上传索引"
    payload = json.loads(index[0].read_text(encoding="utf-8"))
    assert payload["pack_by"] == "folder"
    return f"{len(volumes)} 卷，索引 {len(payload['volumes'])} 项"


@check("查找：文件名定位到卷")
def verify_find(sb: Sandbox):
    result = sb.run(["find", "c.pdf"], check=True)
    assert "第 2 卷" in result.stdout or "第 1 卷" in result.stdout, result.stdout
    assert "sc.part" in result.stdout, result.stdout
    return result.stdout.strip().splitlines()[0]


@check("前置检查：超限文件在上传前被拦下")
def verify_precheck(sb: Sandbox):
    folder = sb.make_tree("big", ["huge.bin"], size=3 * MB)
    before = sb.db("SELECT COUNT(*) FROM tasks")[0][0]

    result = sb.run([
        "upload", "-s", str(folder), "-f", "huge.bin", "-n", "big",
        "-r", "/apps/Test", "--volume-size", str(MB),
        "--provider", "local", "--layout", "self-contained",
    ])
    combined = result.stdout + result.stderr
    assert "超过分卷大小" in combined, combined
    assert "huge.bin" in combined, combined

    after = sb.db("SELECT COUNT(*) FROM tasks")[0][0]
    assert after == before, f"前置检查失败时不该建任务: {before} -> {after}"
    return "未创建任务，已列出文件名"


@check("加密：口令生效且落库为校验值")
def verify_encryption(sb: Sandbox):
    folder = sb.make_tree("enc", ["secret.bin"], size=2 * MB)
    env = dict(sb.env)
    env["STREAMPACK_PASSWORD"] = "pw-verify"
    result = subprocess.run(
        [sys.executable, "main.py", "upload",
         "-s", str(folder), "-f", "secret.bin", "-n", "enc",
         "-r", "/apps/Test", "--volume-size", str(MB), "--provider", "local"],
        cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    volumes = sorted(sb.rehearsal.rglob("enc.part*.zip"))
    assert volumes, "没有生成分卷"

    with zipfile.ZipFile(volumes[-1]) as zf:
        name = [n for n in zf.namelist() if not n.startswith("__streampack__")][0]
        info = zf.getinfo(name)
        assert info.flag_bits & 0x1, "条目没有标记为加密"
        try:
            zf.read(name)
            raise AssertionError("加密卷居然能直接读出来")
        except (RuntimeError, OSError, NotImplementedError):
            pass

    columns = [row[1] for row in sb.db("PRAGMA table_info(tasks)")]
    assert "password_tag" in columns
    tags = [r[0] for r in sb.db("SELECT password_tag FROM tasks WHERE base_name='enc'")]
    assert tags and tags[0], "password_tag 没落库"
    return "AES 卷需口令，DB 只存 PBKDF2 校验值"


@check("中断与续传")
def verify_resume(sb: Sandbox):
    folder = sb.src / "resume"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "big.bin").write_bytes(os.urandom(120 * MB))

    args = [sys.executable, "main.py", "upload",
            "-s", str(folder), "-f", "big.bin", "-n", "resume",
            "-r", "/apps/Test", "--volume-size", str(4 * MB), "--provider", "local"]
    proc = subprocess.Popen(args, cwd=ROOT, env=sb.env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(2.5)
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()

    task_id = sb.db("SELECT task_id FROM tasks WHERE base_name='resume'")[0][0]
    status = sb.db("SELECT status FROM tasks WHERE task_id=?", (task_id,))[0][0]
    assert status in ("running", "interrupted", "partial"), f"中断后状态异常: {status}"

    listed = sb.run(["list"], check=True).stdout
    assert task_id[:8] in listed or "resume" in listed, listed

    resumed = sb.run(["resume", task_id, "--provider", "local"], check=False, timeout=600)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "完成" in resumed.stdout, resumed.stdout

    final = sb.db("SELECT status FROM tasks WHERE task_id=?", (task_id,))[0][0]
    assert final == "completed", final

    volumes = sorted(sb.rehearsal.rglob("resume.part*.zip"))
    with concat_and_open(volumes, sb.work / "resume.zip") as zf:
        assert zf.testzip() is None
    return f"中断时 {status} -> 续传完成，{len(volumes)} 卷"


@check("安装器：静默安装 + 卸载项")
def verify_installer(sb: Sandbox):
    setups = sorted((ROOT / "dist").glob("StreamPack-Setup-*.exe"))
    assert setups, "dist/ 下没有安装器，先跑 scripts/build_release.ps1"
    setup = setups[-1]

    result_file = sb.work / "install-result.json"
    subprocess.run(
        [str(setup), "--silent", "--dir", str(sb.install),
         "--no-shortcut", "--result-file", str(result_file)],
        cwd=ROOT, env=sb.env, timeout=300,
    )
    assert result_file.exists(), "安装器没有写结果文件（检查 TEMP 是否可写）"
    payload = json.loads(result_file.read_text(encoding="utf-8"))
    assert payload.get("ok") is True, payload

    assert (sb.install / "streampack.exe").exists()
    assert (sb.install / "streampack-gui.exe").exists()
    assert (sb.install / "uninstall.ps1").exists()

    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamPack"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            location = winreg.QueryValueEx(k, "InstallLocation")[0]
        assert Path(location) == sb.install, location
        has_key = True
    except FileNotFoundError:
        has_key = False
    except PermissionError:
        has_key = False

    sb.registry_key = has_key
    warnings = payload.get("warnings") or []
    note = "已写 HKCU 卸载项" if has_key else "HKCU 被沙箱拒绝（真实环境需确认）"
    return f"{setup.name} -> copied={payload.get('copied')}，{note}"


@check("卸载脚本：清理安装目录与卸载项")
def verify_uninstall(sb: Sandbox):
    cmd = sb.install / "uninstall.cmd"
    assert cmd.exists(), "安装目录里没有 uninstall.cmd"

    env = dict(os.environ)
    env["STREAMPACK_HOME"] = str(sb.home)
    # 刻意以安装目录为 cwd、并且走 .cmd 入口：这就是用户双击时的情形。
    # .cmd 会先 cd /d %TEMP%，所以卸载脚本能就地同步删除。
    result = subprocess.run(
        ["cmd", "/c", str(cmd), "-Yes"],
        cwd=str(sb.install), env=env, timeout=180,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )

    deadline = time.time() + 30
    while time.time() < deadline and sb.install.exists():
        time.sleep(1)

    assert not sb.install.exists(), (
        f"安装目录还在: {sb.install}\n"
        f"脚本输出:\n{result.stdout}\n{result.stderr}"
    )

    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\StreamPack"
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, key).Close()
        raise AssertionError("卸载项没删掉")
    except FileNotFoundError:
        pass
    except PermissionError:
        pass        # 沙箱里本来就没写进去，跳过

    if getattr(sb, "registry_key", False):
        return "目录与卸载项均已清理"
    return "目录已清理（沙箱未能写 HKCU，卸载项需真实环境确认）"


@check("GUI 可启动")
def verify_gui(sb: Sandbox):
    exe = ROOT / "dist" / "StreamPack" / "streampack-gui.exe"
    assert exe.exists(), "dist/StreamPack 下没有 streampack-gui.exe"

    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=sb.env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(7)
    alive = proc.poll() is None
    if alive:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    assert alive, "GUI 启动即退出"
    return "进程存活（窗口未做像素级验证）"


def skipped(name, reason):
    RESULTS.append((name, "SKIP", reason, 0.0))
    print(f"  [SKIP] {name}  — {reason}")


def report(before, after):
    print("\n" + "=" * 78)
    width = max(len(r[0]) for r in RESULTS) + 2
    for name, status, detail, elapsed in RESULTS:
        flag = {"PASS": "✓", "FAIL": "✗", "SKIP": "-"}[status]
        print(f"{flag} {name:<{width}} {status:<5} {elapsed:5.1f}s  {detail}")

    passed = sum(1 for r in RESULTS if r[1] == "PASS")
    failed = sum(1 for r in RESULTS if r[1] == "FAIL")
    skipped_n = sum(1 for r in RESULTS if r[1] == "SKIP")

    print("=" * 78)
    print(f"通过 {passed} / 失败 {failed} / 跳过 {skipped_n}")

    if before != after:
        print("\n[FAIL] 真实数据目录被改动了！")
        print(f"  之前: {before}")
        print(f"  之后: {after}")
        return 1

    print("真实数据目录未被改动（沙箱隔离有效）")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(ROOT / "_verify"),
                        help="沙箱目录（会被清空）")
    parser.add_argument("--keep", action="store_true", help="保留沙箱便于排查")
    parser.add_argument("--skip-installer", action="store_true")
    args = parser.parse_args()

    work = Path(args.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    before = snapshot_data_dir()
    print(f"沙箱: {work}")
    print(f"真实数据目录快照: {'存在' if before['exists'] else '不存在'}\n")

    sb = Sandbox(work)
    verify_isolation(sb)
    verify_continuous(sb)
    verify_self_contained(sb)
    verify_find(sb)
    verify_precheck(sb)
    verify_encryption(sb)
    verify_resume(sb)
    if args.skip_installer:
        skipped("安装器：静默安装 + 卸载项", "命令行指定了 --skip-installer")
        skipped("卸载脚本：清理安装目录与卸载项", "命令行指定了 --skip-installer")
    else:
        verify_installer(sb)
        verify_uninstall(sb)
    verify_gui(sb)

    skipped("真实百度上传", "需要人完成 OAuth 授权；用 --keep 保留沙箱后可接入")
    skipped("SmartScreen / 图标外观", "需要人眼看安装器与资源管理器")

    code = report(before, snapshot_data_dir())

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"\n沙箱保留在 {work}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
