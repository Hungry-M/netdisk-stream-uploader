# tests/test_packaging.py
"""
打包契约。

这些断言看起来"只是读文本"，但每一条都对应一次真实事故：
GUI 启动即崩、exe 没有图标、PowerShell 脚本在 5.1 下语法错误。
"""
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_gui_spec_declares_future_hidden_import():
    """shiboken6 运行时才 import __future__，不显式声明 GUI 会启动即崩"""
    spec = (ROOT / "packaging" / "streampack.spec").read_text(encoding="utf-8")
    assert '__future__' in spec
    assert "hiddenimports=" in spec


def test_specs_reference_the_icon():
    for name in ("streampack.spec", "installer.spec"):
        spec = (ROOT / "packaging" / name).read_text(encoding="utf-8")
        assert "streampack.ico" in spec, f"{name} 没有引用图标"


def test_icon_exists_and_is_multi_size():
    ico = ROOT / "assets" / "streampack.ico"
    assert ico.is_file(), "缺少 assets/streampack.ico（python scripts/make_icon.py）"

    reserved, kind, count = struct.unpack("<HHH", ico.read_bytes()[:6])
    assert reserved == 0 and kind == 1, "不是合法的 .ico"
    assert count >= 4, f"只有 {count} 个尺寸，小图标会糊"


def test_specs_bundle_locale_and_assets():
    spec = (ROOT / "packaging" / "streampack.spec").read_text(encoding="utf-8")
    assert '"locale"' in spec
    assert '"assets"' in spec


def test_powershell_scripts_have_bom_when_non_ascii():
    """Windows PowerShell 5.1 会按 GBK 解析无 BOM 的 UTF-8，中文注释会炸语法"""
    for path in list((ROOT / "scripts").glob("*.ps1")) + list((ROOT / "packaging").glob("*.ps1")):
        data = path.read_bytes()
        has_bom = data[:3] == b"\xef\xbb\xbf"
        non_ascii = any(b > 0x7F for b in data)
        if non_ascii:
            assert has_bom, f"{path.name} 含非 ASCII 但缺 BOM"


def test_portable_extras_are_listed_in_build_script():
    script = (ROOT / "scripts" / "build_release.ps1").read_text(encoding="utf-8-sig")
    for name in ("cleanup.ps1", "cleanup.cmd", "uninstall.ps1", "uninstall.cmd",
                 "trust-sign.ps1", "trust-sign.cmd"):
        assert name in script, f"便携包漏了 {name}"


def test_installer_starts_from_its_own_directory():
    """安装器必须能把自身目录里的 payload 复制出去，路径不能算错"""
    spec = (ROOT / "packaging" / "installer.spec").read_text(encoding="utf-8")
    assert "installer.py" in spec
    assert "StreamPack-Setup-" in spec
