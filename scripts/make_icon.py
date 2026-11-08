#!/usr/bin/env python
"""
生成 assets/streampack.ico。

设计：蓝色圆角方块 + 横杠，表示"层叠的分卷"。
- 16/24px 用两条粗杠，否则糊成一团
- 所有尺寸都用 DIB（未压缩）帧，不用 PNG 帧

为什么不用 PNG 帧：PNG 压缩帧在 Windows 资源管理器的小尺寸渲染上兼容性差，
部分环境会退回成默认图标。DIB 帧体积大一点，但到哪都能显示。
"""
import struct
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


def dib_frame(img: Image.Image) -> bytes:
    """把 RGBA 图转成 ICO 里的 DIB：BITMAPINFOHEADER + BGRA(XOR) + AND 掩码。"""
    size = img.width
    px = img.load()

    header = struct.pack(
        "<IiiHHIIiiII",
        40,              # biSize
        size,            # biWidth
        size * 2,        # biHeight：XOR 图 + AND 掩码
        1,               # biPlanes
        32,              # biBitCount
        0,               # biCompression = BI_RGB
        0,               # biSizeImage
        0, 0, 0, 0,      # 分辨率与调色板
    )

    # XOR：自下而上，BGRA
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))

    # AND 掩码：每行按 4 字节对齐，1 表示透明
    row_bytes = ((size + 31) // 32) * 4
    and_mask = bytearray()
    for y in range(size - 1, -1, -1):
        row = bytearray(row_bytes)
        for x in range(size):
            if px[x, y][3] == 0:
                row[x // 8] |= 0x80 >> (x % 8)
        and_mask += row

    return header + bytes(xor) + bytes(and_mask)


def build_ico(frames) -> bytes:
    """小尺寸用 DIB 帧（兼容性最好），256 用 PNG 帧（省体积，Vista+ 都支持）。"""
    import io

    images = []
    for img in frames:
        if img.width >= 256:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            images.append((img.width, buf.getvalue()))
        else:
            images.append((img.width, dib_frame(img)))

    out = struct.pack("<HHH", 0, 1, len(images))     # 保留位、类型=图标、数量
    offset = 6 + 16 * len(images)

    entries = b""
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,      # 0 表示 256
            size if size < 256 else 0,
            0,                              # 调色板数
            0,                              # 保留
            1,                              # 色彩平面
            32,                             # 位深
            len(data),
            offset,
        )
        offset += len(data)

    return out + entries + b"".join(data for _size, data in images)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [draw(n) for n in SIZES]
    OUT.write_bytes(build_ico(frames))
    print(f"已生成 {OUT} ({OUT.stat().st_size} 字节，{len(SIZES)} 种尺寸，DIB 帧)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
