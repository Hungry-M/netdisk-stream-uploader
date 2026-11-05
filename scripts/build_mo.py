#!/usr/bin/env python
"""
把 locale/<lang>/LC_MESSAGES/*.po 编译成 .mo。

为什么要自己写：本机没有 msgfmt 模块，PySide6 自带的 lrelease 产出的是
Qt 的 .qm 格式，gettext 读不了。.mo 的格式很小，直接用标准库生成即可。

用法：
    python scripts/build_mo.py            # 编译 locale 下所有 .po
    python scripts/build_mo.py --check    # 只检查，不写文件
"""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALE_DIR = ROOT / "locale"


def parse_po(path: Path):
    """解析 .po，返回 [(msgid, msgstr)]。支持 header 与相邻引号续行。"""
    entries = []
    msgid, msgstr = "", ""
    field = None
    in_entry = False

    def flush():
        nonlocal msgid, msgstr, field, in_entry
        if in_entry:
            entries.append((msgid, msgstr))
        msgid = msgstr = ""
        field = None
        in_entry = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("#"):
            continue
        if line.startswith("msgid "):
            if in_entry and msgid:
                flush()                     # 连续两条之间没有空行
            msgid = _unquote(line[len("msgid "):])
            field = "msgid"
            in_entry = True
        elif line.startswith("msgstr "):
            msgstr = _unquote(line[len("msgstr "):])
            field = "msgstr"
        elif line.startswith('"'):
            text = _unquote(line)
            if field == "msgid":
                msgid += text
            elif field == "msgstr":
                msgstr += text
    flush()
    return entries


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return (text.replace('\\n', '\n')
                .replace('\\t', '\t')
                .replace('\\"', '"')
                .replace('\\\\', '\\'))


def build_mo(entries) -> bytes:
    """按 GNU gettext 的 .mo 格式打包。"""
    # msgid 必须按字节序排序
    items = sorted((k.encode("utf-8"), v.encode("utf-8")) for k, v in entries)
    count = len(items)

    header_size = 7 * 4
    orig_table_off = header_size
    trans_table_off = orig_table_off + count * 8
    strings_off = trans_table_off + count * 8

    orig_blob = b""
    orig_table = []
    for msgid, _ in items:
        orig_table.append((len(msgid), strings_off + len(orig_blob)))
        orig_blob += msgid + b"\x00"

    trans_blob = b""
    trans_table = []
    trans_base = strings_off + len(orig_blob)
    for _, msgstr in items:
        trans_table.append((len(msgstr), trans_base + len(trans_blob)))
        trans_blob += msgstr + b"\x00"

    out = struct.pack("<7I", 0x950412DE, 0, count, orig_table_off,
                      trans_table_off, 0, 0)
    for length, offset in orig_table:
        out += struct.pack("<2I", length, offset)
    for length, offset in trans_table:
        out += struct.pack("<2I", length, offset)
    out += orig_blob + trans_blob
    return out


def main(argv):
    check_only = "--check" in argv
    if not LOCALE_DIR.is_dir():
        print(f"没有 locale 目录: {LOCALE_DIR}")
        return 1

    total = 0
    for po in sorted(LOCALE_DIR.glob("*/LC_MESSAGES/*.po")):
        # header 条目（msgid 为空）必须保留：gettext 靠它识别编码，
        # 丢了会按 ascii 解析，中文 msgid 直接报 UnicodeDecodeError
        entries = parse_po(po)
        translated = len([e for e in entries if e[0]])
        total += translated

        if check_only:
            print(f"{po.relative_to(ROOT)}: {translated} 条")
            continue

        mo = po.with_suffix(".mo")
        mo.write_bytes(build_mo(entries))
        print(f"{po.relative_to(ROOT)} -> {mo.name} ({translated} 条)")

    if total == 0:
        print("没有找到任何 .po")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
