# -*- coding: utf-8 -*-
"""
Brand mark: a graphite tile with inspection brackets framing an upward trend
line — "precise inspection that leads to measurable improvement".

The mark is defined once as vector geometry and rendered for the splash,
sign-in screen, sidebar, window icon and the Windows .ico file.
"""

import os
import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

INK = "#18181B"
SIGNAL = "#5B8CFF"


def logo_svg(simplified=False, tile=INK, line=SIGNAL):
    brackets = "" if simplified else (
        '<g stroke="#FFFFFF" stroke-width="4" stroke-linecap="round" fill="none">'
        '<path d="M14 23v-7a2 2 0 0 1 2-2h7"/><path d="M41 14h7a2 2 0 0 1 2 2v7"/>'
        '<path d="M50 41v7a2 2 0 0 1-2 2h-7"/><path d="M23 50h-7a2 2 0 0 1-2-2v-7"/></g>')
    width = "7" if simplified else "4.5"
    pts = "14,46 26,34 34,40 50,22" if simplified else "21,42 29,34 35,38 44,27"
    dot = "" if simplified else f'<circle cx="44" cy="27" r="3.4" fill="{line}"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            f'<rect width="64" height="64" rx="14" fill="{tile}"/>{brackets}'
            f'<polyline points="{pts}" fill="none" stroke="{line}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>{dot}</svg>')


def logo_image(size):
    renderer = QSvgRenderer(QByteArray(logo_svg(simplified=size <= 24).encode()))
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def logo_pixmap(size, dpr=1.0):
    px = QPixmap.fromImage(logo_image(int(size * dpr)))
    px.setDevicePixelRatio(dpr)
    return px


def app_icon():
    ic = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        ic.addPixmap(QPixmap.fromImage(logo_image(s)))
    return ic


def write_ico(path, sizes=(16, 20, 24, 32, 40, 48, 64, 128, 256)):
    """Multi-resolution .ico with PNG-compressed entries (Vista+ format)."""
    blobs = []
    for s in sizes:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        logo_image(s).save(buf, "PNG")
        blobs.append((s, bytes(buf.data())))
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b"", b""
    for s, png in blobs:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(header + entries + data)
