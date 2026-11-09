#!/usr/bin/env python
"""
生成一套固定内容的测试数据，并把可复制的命令打印出来。

    python scripts/make_test_data.py                      # 默认 D:\\streampack-test\\data
    python scripts/make_test_data.py --out D:\\mydata --seed 7

内容（确定性：同一 seed 生成同样的字节，方便事后比对 SHA-256）：

    photos/  3 个 2 MB
    docs/    1 个 1 MB + 1 个 200 KB
    flat/    2 个 512 KB
    big/     1 个 8 MB   （用来触发"单文件超过分卷"和跨卷切分）
"""
import argparse
import random
from pathlib import Path

LAYOUT = {
    "photos": [("photo_01.jpg", 2 * 1024 * 1024),
               ("photo_02.jpg", 2 * 1024 * 1024),
               ("photo_03.jpg", 2 * 1024 * 1024)],
    "docs": [("manual.pdf", 1024 * 1024),
             ("notes.txt", 200 * 1024)],
    "flat": [("a.bin", 512 * 1024),
             ("b.bin", 512 * 1024)],
    "big": [("movie.bin", 8 * 1024 * 1024)],
}


def build(out: Path, seed: int, force: bool) -> None:
    if out.exists() and any(out.iterdir()) and not force:
        raise SystemExit(f"{out} 已存在且非空；加 --force 覆盖，或换个 --out")

    rng = random.Random(seed)
    for folder, files in LAYOUT.items():
        target = out / folder
        target.mkdir(parents=True, exist_ok=True)
        for name, size in files:
            (target / name).write_bytes(rng.randbytes(size))


def human(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=r"D:\streampack-test\data")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    build(out, args.seed, args.force)

    print(f"已生成测试数据: {out}（seed={args.seed}）\n")
    total = 0
    for folder, files in LAYOUT.items():
        summary = "，".join(f"{n} {human(s)}" for n, s in files)
        size = sum(s for _n, s in files)
        total += size
        print(f"  {folder + '/':<10} {human(size):>8}   {summary}")
    print(f"  {'合计':<10} {human(total):>8}\n")

    data = str(out)
    print("把 <你的应用名> 换成你注册的百度应用名，然后按需执行：\n")
    print("# 0) 沙箱模式（不碰真实数据；想测真实数据就跳过这两行）")
    print(r'$env:STREAMPACK_HOME   = "D:\streampack-test\home"')
    print(r'$env:STREAMPACK_CONFIG = "D:\streampack-test\home\config.json"')
    print()
    print("# 1) 声明应用名（只需一次）")
    print("python main.py config set app_name <你的应用名>")
    print()
    print("# 2) 连续流：小文件，4 MB 分卷（默认布局）")
    print(f'python main.py upload -s "{data}" -f "photos,docs,flat" -n smoke-cont --volume-size 4194304')
    print()
    print("# 3) 自包含 + 文件夹优先：每卷可单独解压，photos/docs 各自成卷")
    print(f'python main.py upload -s "{data}" -f "photos,docs" -n smoke-sc --volume-size 4194304 --layout self-contained --pack-by folder')
    print()
    print("# 4) 超限前置检查：8 MB 文件 + 2 MB 分卷 → 应在建任务前报错")
    print(f'python main.py upload -s "{data}" -f "big" -n smoke-big --volume-size 2097152 --layout self-contained')
    print()
    print("# 5) 大文件跨卷：8 MB 文件 + 2 MB 分卷（连续流，拼接后可解压）")
    print(f'python main.py upload -s "{data}" -f "big" -n smoke-split --volume-size 2097152')
    print()
    print("# 6) 找文件在第几卷（3 号任务完成后才有映射）")
    print("python main.py find photo_02")
    print()
    print("# 7) 核对下载回来的内容（先下载并拼接成 combined.zip）")
    print(f'python -c "import zipfile,hashlib,pathlib; z=zipfile.ZipFile(r\'combined.zip\'); '
          f'print([(n, hashlib.sha256(z.read(n)).hexdigest()[:16]) for n in z.namelist() if not n.startswith(\'__\')])"')
    print(f'# 与源文件比对：{out}\\photos\\photo_01.jpg 等的 sha256')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
