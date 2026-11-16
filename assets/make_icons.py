"""Regenerate the PNG + ICO files from the SVG sources in this folder.

    python assets/make_icons.py

Needs only PySide6 (QtSvg). Edit icon.svg / icon_small.svg, run this, done.
Sizes up to 32 px use the simplified icon_small.svg (readable when tiny);
larger sizes use the detailed icon.svg.
"""
import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
SMALL_MAX = 32
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(svg_path: str, size: int) -> QImage:
    renderer = QSvgRenderer(svg_path)
    if not renderer.isValid():
        raise SystemExit(f"invalid SVG: {svg_path}")
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(p)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def write_ico(path: str, images: dict) -> None:
    """Write a multi-size .ico with PNG-compressed entries (no Pillow needed)."""
    entries = sorted(images.items())
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory, blobs = b"", b""
    for size, data in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, "wb") as fh:
        fh.write(header + directory + blobs)


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    full = os.path.join(HERE, "icon.svg")
    small = os.path.join(HERE, "icon_small.svg")
    ico = {}
    for size in SIZES:
        data = png_bytes(render(small if size <= SMALL_MAX else full, size))
        with open(os.path.join(HERE, f"icon_{size}.png"), "wb") as fh:
            fh.write(data)
        if size in ICO_SIZES:
            ico[size] = data
        print(f"icon_{size}.png")
    write_ico(os.path.join(HERE, "icon.ico"), ico)
    print("icon.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())
