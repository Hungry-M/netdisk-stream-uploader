# packaging/installer.py
"""
StreamPack 安装器（PyInstaller onefile）。

设计取舍：
- 不引入额外安装框架（Inno/NSIS），产物和项目同一套工具链；
- 窗口化进程里不能靠 print 反馈（可能直接 OSError），所以用 MessageBox，
  自动化场景靠 `--result-file` 的 JSON 与退出码；
- 按 dev-notes 的规范：装到 %LOCALAPPDATA%\\Programs，不要求管理员权限，
  写 HKCU 卸载项，不自动安装任何证书。

用法：
    StreamPack-Setup.exe                           交互式安装
    StreamPack-Setup.exe --silent                  静默安装
    StreamPack-Setup.exe --silent --dir D:\\Apps\\StreamPack
    StreamPack-Setup.exe --silent --result-file r.json
    StreamPack-Setup.exe --silent --no-shortcut
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "StreamPack"
GUI_EXE = "streampack-gui.exe"
CLI_EXE = "streampack.exe"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"

from src.version import __version__  # noqa: E402  （打包时随 src 一起带上）


# ---------- 纯函数（可单测） ----------

def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Programs" / APP_NAME


def payload_dir() -> Path:
    """onefile 解包后内容在 _MEIPASS/StreamPack；源码运行时回落到 dist/。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / APP_NAME
    return Path(__file__).resolve().parent.parent / "dist" / APP_NAME


def start_menu_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def shortcut_path() -> Path:
    return start_menu_dir() / f"{APP_NAME}.lnk"


def copy_payload(src: Path, dest: Path) -> int:
    """把安装包里的内容复制到目标目录，返回顶层条目数。"""
    if not src.is_dir():
        raise FileNotFoundError(f"安装包内容缺失: {src}")

    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
        count += 1
    return count


def shortcut_command(dest: Path, lnk: Path, exe: str = GUI_EXE) -> str:
    """生成创建快捷方式用的 PowerShell 脚本（不执行，便于测试）。"""
    target = dest / exe
    return (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath='{target}';$s.WorkingDirectory='{wd}';"
        "$s.IconLocation='{target}';$s.Save()"
    ).format(lnk=lnk, target=target, wd=dest)


def create_shortcut(dest: Path, lnk: Path, exe: str = GUI_EXE) -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", shortcut_command(dest, lnk, exe)],
        check=True, capture_output=True,
    )


def uninstall_command(dest: Path) -> str:
    script = dest / "uninstall.ps1"
    return f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}"'


def register_uninstall(dest: Path, version: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(dest))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, uninstall_command(dest))
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def write_result(path: str, payload: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def message(text: str, title: str = APP_NAME) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    except Exception:
        pass


# ---------- 安装流程 ----------

def install(dest: Path, with_shortcut: bool = True) -> dict:
    """
    复制 → 快捷方式 → 卸载项。

    只有复制失败才算安装失败：快捷方式和注册表是"锦上添花"，
    在受限令牌或策略环境下可能被拒，但程序本身已经可用，不该整体判失败。
    """
    copied = copy_payload(payload_dir(), dest)

    warnings = []
    shortcut = None

    if with_shortcut:
        try:
            create_shortcut(dest, shortcut_path())
            shortcut = str(shortcut_path())
        except Exception as e:                  # noqa: BLE001
            warnings.append(f"快捷方式创建失败：{e}")

    try:
        register_uninstall(dest, __version__)
    except Exception as e:                      # noqa: BLE001
        warnings.append(f"卸载项写入失败：{e}（仍可用安装目录里的 uninstall.cmd 卸载）")

    return {
        "ok": True,
        "version": __version__,
        "install_dir": str(dest),
        "copied": copied,
        "shortcut": shortcut,
        "warnings": warnings,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, add_help=True)
    parser.add_argument("--silent", action="store_true", help="不弹任何对话框")
    parser.add_argument("--dir", default=None, help="安装目录（默认 %%LOCALAPPDATA%%\\Programs\\StreamPack）")
    parser.add_argument("--result-file", default=None, help="把结果写成 JSON")
    parser.add_argument("--no-shortcut", action="store_true", help="不创建开始菜单快捷方式")
    args = parser.parse_args(argv)

    dest = Path(args.dir) if args.dir else default_install_dir()
    result = {"ok": False, "version": __version__, "install_dir": str(dest)}

    try:
        result = install(dest, with_shortcut=not args.no_shortcut)
    except Exception as e:                      # noqa: BLE001 —— 安装失败必须如实上报
        result["error"] = str(e)
        if args.result_file:
            write_result(args.result_file, result)
        if not args.silent:
            message(f"安装失败：\n{e}", f"{APP_NAME} 安装")
        return 1

    if args.result_file:
        write_result(args.result_file, result)

    if not args.silent:
        lines = [
            f"已安装到：\n{dest}\n",
            f"开始菜单里有 {APP_NAME} 快捷方式。\n",
            f"卸载：Windows 设置 → 应用 → {APP_NAME}，或运行安装目录里的 uninstall.cmd。",
        ]
        if result.get("warnings"):
            lines.append("\n注意：\n" + "\n".join(f"· {w}" for w in result["warnings"]))
        message("\n".join(lines), f"{APP_NAME} {__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
