#!/usr/bin/env python
"""
生成 assets/streampack.ico。

设计：蓝色圆角方块 + 三条白色横杠，表示"层叠的分卷"。
16px 下横杠要够粗、间距要够大，否则糊成一团。

用法：python scripts/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "streampack.ico"

ACCENT = (0, 120, 212, 255)      # #0078D4
WHITE = (255, 255, 255, 255)
SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw(size: int) -> Image.Image:
    scale = 4                        # 先画大图再缩小，边缘更干净
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    radius = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=ACCENT)

    # 16/24px 上三条杠会糊成一团，改成两条更粗的
    widths = [0.54, 0.34] if size <= 24 else [0.52, 0.40, 0.28]
    bar_h = max(int(s * (0.16 if size <= 24 else 0.11)), 2 * scale)
    gap = int(s * (0.12 if size <= 24 else 0.10))
    total = len(widths) * bar_h + (len(widths) - 1) * gap
    y = (s - total) // 2

    for w_ratio in widths:
        w = int(s * w_ratio)
        x0 = (s - w) // 2
        d.rounded_rectangle([x0, y, x0 + w, y + bar_h],
                            radius=bar_h // 2, fill=WHITE)
        y += bar_h + gap

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [draw(n) for n in SIZES]

    # 用最大的一张作为基准，其余作为多尺寸帧写入 .ico
    frames[-1].save(OUT, format="ICO", sizes=[(n, n) for n in SIZES],
                    append_images=frames[:-1])
    print(f"已生成 {OUT} ({OUT.stat().st_size} 字节，{len(SIZES)} 种尺寸)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
