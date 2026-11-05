#!/usr/bin/env python
"""
从源码里提取 gettext 的 msgid，并与 .po 对比。

为什么要用 AST 而不是 grep：_f("...{0}...") 这类调用里，msgid 可能是
跨行拼接的多个字符串字面量，正则抓不全；ast 会把它们合并成一个常量。

用法：
    python scripts/extract_strings.py            # 列出所有 msgid
    python scripts/extract_strings.py --missing  # 只列 .po 里缺的
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [ROOT / "src", ROOT / "main.py"]
PO = ROOT / "locale" / "en" / "LC_MESSAGES" / "streampack.po"

TRANSLATE_FUNCS = {"_", "_f"}
# click 用函数的 docstring 当命令说明，而 docstring 没法包进 _()，
# 只能在定义后统一翻译；抽取时要把它们也算进来。
CLICK_DECORATORS = {"command", "group"}


def _is_click_decorator(node) -> bool:
    if isinstance(node, ast.Call):
        node = node.func
    name = getattr(node, "id", None) or getattr(node, "attr", None)
    return name in CLICK_DECORATORS


def iter_py_files():
    for target in SOURCES:
        if target.is_file():
            yield target
        else:
            yield from target.rglob("*.py")


def collect(paths) -> dict:
    """返回 {msgid: 首次出现的 文件:行号}"""
    found = {}
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            print(f"跳过无法解析的文件 {path}: {e}", file=sys.stderr)
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(_is_click_decorator(d) for d in node.decorator_list):
                    doc = ast.get_docstring(node)
                    if doc:
                        found.setdefault(
                            doc.strip(), f"{path.relative_to(ROOT)}:{node.lineno}")

            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name not in TRANSLATE_FUNCS or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                key = first.value
                found.setdefault(key, f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def po_msgids() -> set:
    # scripts 不是包，直接按同目录导入
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_mo import parse_po  # noqa: E402

    if not PO.exists():
        return set()
    return {msgid for msgid, _ in parse_po(PO) if msgid}


def main(argv):
    found = collect(iter_py_files())
    existing = po_msgids()

    if "--missing" in argv:
        missing = {k: v for k, v in found.items() if k not in existing}
        for key, where in sorted(missing.items()):
            print(f"{where}\t{key!r}")
        print(f"\n共 {len(missing)} 条缺失（源码 {len(found)} 条，.po {len(existing)} 条）")
        return 0 if not missing else 1

    for key, where in sorted(found.items()):
        mark = " " if key in existing else "!"
        print(f"{mark} {where}\t{key!r}")
    print(f"\n源码 {len(found)} 条，.po {len(existing)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
