#!/usr/bin/env python
"""
实测并行上传的加速比：用一个固定延迟的假 provider 模拟网络。

    python scripts/bench_upload.py
    python scripts/bench_upload.py --volumes 16 --latency 0.5

只在本目录下建临时文件，跑完自删；不联网、不碰用户数据。

为什么需要它：压缩侧的吞吐（scripts/bench_stream.py）和上传侧是两回事。
上传是线程池并发，实际加速比取决于"每卷耗时 × 卷数 / 线程数"，
用真实网盘测不准（还受限流影响），所以这里用固定延迟把网络抽象掉。
"""
import argparse
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config.settings import ResourceSettings           # noqa: E402
from src.core.uploader import VolumeUploader               # noqa: E402
from src.hardware.resource_manager import ResourceManager  # noqa: E402
from src.storage.base import Capabilities                  # noqa: E402

TMP = ROOT / "_bench_upload"


class SlowProvider:
    """每个分卷固定耗时，并记录真实峰值并发数。"""

    name = "slow"
    capabilities = Capabilities()
    default_root = "/remote"

    def __init__(self, latency: float):
        self.latency = latency
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.uploaded = 0

    def authenticate(self, interactive: bool = True) -> None:
        pass

    def ensure_dir(self, remote_dir: str) -> None:
        pass

    def upload_file(self, remote_path, fileobj, size=None) -> None:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            fileobj.read()
            time.sleep(self.latency)
            with self.lock:
                self.uploaded += 1
        finally:
            with self.lock:
                self.active -= 1

    def list_dir(self, remote_dir):
        return []

    def delete_file(self, remote_path) -> None:
        pass


def run(workers: int, volumes: int, latency: float, backpressure=None):
    TMP.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(volumes):
        p = TMP / f"vol{i:03d}.zip"
        p.write_bytes(b"x" * 1024)
        paths.append(p)

    provider = SlowProvider(latency)
    settings = ResourceSettings(max_parallel_workers=workers,
                                preferred_temp_dirs=[str(TMP)])
    uploader = VolumeUploader(provider, ResourceManager(settings=settings))

    def gen():
        for p in paths:
            yield open(p, "rb")

    started = time.time()
    uploader.upload_all(gen(), "/remote", "bench", task_id=None,
                        backpressure=backpressure)
    elapsed = time.time() - started

    for p in paths:
        p.unlink(missing_ok=True)
    assert provider.uploaded == volumes, provider.uploaded
    return elapsed, provider.peak


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--volumes", type=int, default=8)
    parser.add_argument("--latency", type=float, default=0.3)
    parser.add_argument("--workers", default="1,2,4,8")
    args = parser.parse_args()

    workers_list = [int(x) for x in str(args.workers).split(",") if x.strip()]
    serial = args.volumes * args.latency

    print(f"{args.volumes} 个分卷，每卷 {args.latency}s（串行应约 {serial:.2f}s）")
    print(f"{'线程数':>6} {'实测':>8} {'峰值并发':>8} {'加速比':>8}")
    for workers in workers_list:
        elapsed, peak = run(workers, args.volumes, args.latency)
        print(f"{workers:>6} {elapsed:>7.2f}s {peak:>8} {serial / elapsed:>7.2f}x")

    bp_elapsed, bp_peak = run(4, args.volumes, args.latency, backpressure=2)
    print(f"\n背压=2、4 线程：{bp_elapsed:.2f}s，峰值并发 {bp_peak}"
          f"（在途分卷被限制，磁盘峰值≈2 卷）")

    shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
