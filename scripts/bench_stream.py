#!/usr/bin/env python
"""
流式分卷的实测脚本：峰值磁盘/内存、吞吐、以及"单文件大于分卷"的行为。

    python scripts/bench_stream.py                # 不可压缩数据（deflate 最慢路径）
    python scripts/bench_stream.py --zeros        # 可压缩数据
    python scripts/bench_stream.py --size 256 --volume 64

只在仓库下建临时目录 _bench/，跑完自删；不联网、不写用户目录。
"""
import argparse
import hashlib
import os
import shutil
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config.settings import ResourceSettings          # noqa: E402
from src.core.compressor import compress_and_split        # noqa: E402
from src.hardware.resource_manager import ResourceManager  # noqa: E402

WORK = ROOT / "_bench"
SRC = WORK / "src"
OUT = WORK / "out"
TMP = WORK / "tmp"


def prepare(size_mb: int, zeros: bool) -> Path:
    if WORK.exists():
        shutil.rmtree(WORK)
    for d in (SRC, OUT, TMP):
        d.mkdir(parents=True)

    target = SRC / "big.bin"
    block = b"\x00" * (1024 * 1024)
    with open(target, "wb") as fh:
        for _ in range(size_mb):
            fh.write(block if zeros else os.urandom(1024 * 1024))
    return target


def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=128, help="源文件大小（MB）")
    parser.add_argument("--volume", type=int, default=32, help="分卷大小（MB）")
    parser.add_argument("--zeros", action="store_true", help="用全零数据（可压缩）")
    args = parser.parse_args()

    src_file = prepare(args.size, args.zeros)
    original_hash = hashlib.sha256(src_file.read_bytes()).hexdigest()

    settings = ResourceSettings(preferred_temp_dirs=[str(TMP)])
    rm = ResourceManager(settings=settings)

    proc = psutil.Process()
    peak = {"tmp": 0, "rss": 0}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak["tmp"] = max(peak["tmp"], dir_size(TMP))
            peak["rss"] = max(peak["rss"], proc.memory_info().rss)
            time.sleep(0.02)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()

    started = time.time()
    parts = []
    for volume in compress_and_split(
        str(SRC), ["big.bin"], args.volume * 1024 * 1024, rm,
        fixed_modified_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    ):
        data = volume.read()
        volume.close()
        os.unlink(volume.name)
        parts.append(len(data))
        (OUT / f"part{len(parts):03d}.zip").write_bytes(data)

    elapsed = time.time() - started
    stop.set()
    sampler.join(timeout=1)

    joined = OUT / "joined.zip"
    with open(joined, "wb") as fh:
        for part in sorted(OUT.glob("part*.zip")):
            fh.write(part.read_bytes())

    with zipfile.ZipFile(joined) as zf:
        names = zf.namelist()
        member = [n for n in names if not n.startswith("__streampack__")][0]
        with zf.open(member) as fh:
            ok = hashlib.sha256(fh.read()).hexdigest() == original_hash

    kind = "全零（可压缩）" if args.zeros else "随机（不可压缩）"
    print(f"源文件        : {args.size} MB，{kind}")
    print(f"分卷大小      : {args.volume} MB")
    print(f"分卷数        : {len(parts)}  -> {parts}")
    print(f"归档总大小    : {sum(parts) / 1024 / 1024:.1f} MB")
    print(f"耗时          : {elapsed:.2f} s   吞吐 {args.size / elapsed:.1f} MB/s")
    print(f"峰值临时磁盘  : {peak['tmp'] / 1024 / 1024:.1f} MB")
    print(f"峰值进程内存  : {peak['rss'] / 1024 / 1024:.1f} MB")
    print(f"拼接后校验    : {'一致' if ok else '不一致'}（成员 {member}）")
    if args.size > args.volume:
        print(f"注意：单文件 {args.size} MB > 分卷 {args.volume} MB，"
              f"被切成 {len(parts)} 卷，必须全部拼接才能解压")

    shutil.rmtree(WORK, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
