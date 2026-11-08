# -*- mode: python ; coding: utf-8 -*-
"""
把 dist/StreamPack 整个目录塞进一个 onefile 安装器。

    dist/StreamPack-Setup-<version>.exe

版本号由构建脚本通过环境变量 STREAMPACK_VERSION 传入，避免 spec 里再算一遍。
"""
import os
from pathlib import Path

PKG = Path(SPECPATH).resolve()                  # packaging/ 本身
PROJECT = PKG.parent                            # 项目根
PAYLOAD = PROJECT / "dist" / "StreamPack"

if not PAYLOAD.is_dir():
    raise SystemExit(f"找不到便携包目录，先构建 portable: {PAYLOAD}")

version = os.environ.get("STREAMPACK_VERSION", "dev")

a = Analysis(
    [str(PKG / "installer.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=[(str(PAYLOAD), "StreamPack")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"StreamPack-Setup-{version}",
    console=False,          # 安装器不要黑框，反馈走 MessageBox
    upx=False,
    strip=False,
)
