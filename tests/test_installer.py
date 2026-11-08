# tests/test_installer.py
"""
安装器的纯逻辑。真正写注册表/快捷方式的部分不在这里测——
那属于"在本机装一遍"的手工验证。
"""
import importlib.util
import json
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parent.parent / "packaging" / "installer.py"


def _load():
    spec = importlib.util.spec_from_file_location("streampack_installer", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inst = _load()


def test_default_install_dir_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert inst.default_install_dir() == tmp_path / "Programs" / "StreamPack"


def test_default_install_dir_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert inst.default_install_dir().name == "StreamPack"
    assert inst.default_install_dir().parent.name == "Programs"


def test_shortcut_path_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    lnk = inst.shortcut_path()
    assert lnk == tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "StreamPack.lnk"


def test_payload_dir_falls_back_to_dist(tmp_path):
    # 非 frozen 运行时用 dist/StreamPack
    assert inst.payload_dir().name == "StreamPack"


def test_copy_payload_copies_tree(tmp_path):
    src = tmp_path / "payload"
    (src / "_internal").mkdir(parents=True)
    (src / "streampack.exe").write_bytes(b"exe")
    (src / "_internal" / "lib.dll").write_bytes(b"dll")

    dest = tmp_path / "install"
    count = inst.copy_payload(src, dest)

    assert count == 2
    assert (dest / "streampack.exe").read_bytes() == b"exe"
    assert (dest / "_internal" / "lib.dll").read_bytes() == b"dll"


def test_copy_payload_is_idempotent(tmp_path):
    """重复安装（升级）不能因为目录已存在而失败"""
    src = tmp_path / "payload"
    src.mkdir()
    (src / "a.txt").write_text("1")

    dest = tmp_path / "install"
    inst.copy_payload(src, dest)
    (src / "a.txt").write_text("2")
    inst.copy_payload(src, dest)

    assert (dest / "a.txt").read_text() == "2"


def test_copy_payload_rejects_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        inst.copy_payload(tmp_path / "nope", tmp_path / "install")


def test_shortcut_command_points_at_gui_exe(tmp_path):
    dest = tmp_path / "install"
    lnk = tmp_path / "StreamPack.lnk"
    cmd = inst.shortcut_command(dest, lnk)

    assert "WScript.Shell" in cmd
    assert str(dest / "streampack-gui.exe") in cmd
    assert str(dest) in cmd          # 工作目录
    assert str(lnk) in cmd


def test_uninstall_command_calls_ps1(tmp_path):
    cmd = inst.uninstall_command(tmp_path)
    assert "uninstall.ps1" in cmd
    assert "ExecutionPolicy Bypass" in cmd


def test_write_result(tmp_path):
    out = tmp_path / "nested" / "result.json"
    inst.write_result(str(out), {"ok": True, "version": "1.2.3"})

    assert json.loads(out.read_text(encoding="utf-8")) == {"ok": True, "version": "1.2.3"}


def test_main_reports_failure_to_result_file(tmp_path, monkeypatch):
    """安装失败也要写结果文件，且退出码非 0"""
    monkeypatch.setattr(inst, "payload_dir", lambda: tmp_path / "missing-payload")
    result = tmp_path / "r.json"

    code = inst.main(["--silent", "--dir", str(tmp_path / "install"),
                      "--result-file", str(result)])

    assert code == 1
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert "error" in data


def test_main_installs_into_given_dir(tmp_path, monkeypatch):
    """用一个假 payload 跑完整流程，只验证复制与结果 JSON"""
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "streampack.exe").write_bytes(b"exe")
    monkeypatch.setattr(inst, "payload_dir", lambda: payload)
    monkeypatch.setattr(inst, "register_uninstall", lambda dest, version: None)

    dest = tmp_path / "install"
    result = tmp_path / "r.json"
    code = inst.main(["--silent", "--no-shortcut", "--dir", str(dest),
                      "--result-file", str(result)])

    assert code == 0
    assert (dest / "streampack.exe").read_bytes() == b"exe"
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["install_dir"] == str(dest)
    assert data["shortcut"] is None
    assert data["warnings"] == []


def test_registry_failure_only_warns(tmp_path, monkeypatch):
    """受限环境下写 HKCU 会被拒；程序已经装好，不该整体判失败"""
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "streampack.exe").write_bytes(b"exe")
    monkeypatch.setattr(inst, "payload_dir", lambda: payload)

    def boom(dest, version):
        raise PermissionError("[WinError 5] 拒绝访问。")

    monkeypatch.setattr(inst, "register_uninstall", boom)

    dest = tmp_path / "install"
    result = tmp_path / "r.json"
    code = inst.main(["--silent", "--no-shortcut", "--dir", str(dest),
                      "--result-file", str(result)])

    assert code == 0
    assert (dest / "streampack.exe").exists()
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert any("卸载项写入失败" in w for w in data["warnings"])


def test_shortcut_failure_only_warns(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "streampack.exe").write_bytes(b"exe")
    monkeypatch.setattr(inst, "payload_dir", lambda: payload)
    monkeypatch.setattr(inst, "register_uninstall", lambda dest, version: None)

    def boom(*a, **k):
        raise OSError("no com")

    monkeypatch.setattr(inst, "create_shortcut", boom)

    dest = tmp_path / "install"
    result = tmp_path / "r.json"
    assert inst.main(["--silent", "--dir", str(dest), "--result-file", str(result)]) == 0

    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["shortcut"] is None
    assert any("快捷方式创建失败" in w for w in data["warnings"])
