# -*- coding: utf-8 -*-
"""
Brand mark: a magnifying glass whose lens holds three rising bars, on a deep
blue tile — the audit looks closely at a site, and what it finds makes the
rankings climb. Mint bars are the growth; the white glass is the inspection.

The mark is defined once as vector geometry and rendered for the splash,
sign-in screen, sidebar, window icon and the Windows .ico file. At 24 px and
below a bolder, simpler version keeps it readable in the taskbar.
"""

import os
import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

INK = "#18181B"
TILE_TOP = "#2F5BFF"     # electric blue
TILE_BOTTOM = "#141A4D"  # deep navy
GROWTH = "#3EE6A8"       # mint


def logo_svg(simplified=False):
    tile = (f'<defs><linearGradient id="t" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{TILE_TOP}"/><stop offset="1" stop-color="{TILE_BOTTOM}"/>'
            f'</linearGradient></defs><rect width="64" height="64" rx="15" fill="url(#t)"/>')
    if simplified:  # taskbar / small sizes: thick glass, two bold bars
        body = (f'<rect x="18" y="27" width="6" height="9" rx="1.5" fill="{GROWTH}"/>'
                f'<rect x="27" y="20" width="6" height="16" rx="1.5" fill="{GROWTH}"/>'
                '<circle cx="27" cy="28" r="15" fill="none" stroke="#FFFFFF" stroke-width="6"/>'
                '<path d="M38.5 39.5 52 53" stroke="#FFFFFF" stroke-width="8" stroke-linecap="round"/>')
    else:
        body = (f'<rect x="19" y="29" width="4.2" height="6" rx="1.3" fill="{GROWTH}" opacity="0.75"/>'
                f'<rect x="25.4" y="25" width="4.2" height="10" rx="1.3" fill="{GROWTH}" opacity="0.88"/>'
                f'<rect x="31.8" y="20.5" width="4.2" height="14.5" rx="1.3" fill="{GROWTH}"/>'
                '<circle cx="28" cy="28" r="14" fill="none" stroke="#FFFFFF" stroke-width="4.6"/>'
                '<path d="M38.6 38.6 49.5 49.5" stroke="#FFFFFF" stroke-width="6.4" stroke-linecap="round"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{tile}{body}</svg>'


_MASCOT_PATH = os.path.join(os.path.dirname(__file__), "assets", "mascot.png")
_ICO_PATH = os.path.join(os.path.dirname(__file__), "assets", "app.ico")
_CACHED_MASCOT = None


def _get_mascot_image():
    global _CACHED_MASCOT
    if _CACHED_MASCOT is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(root, "assets", "Cute Sprout Mascot Growth Chart.png"),
            _MASCOT_PATH,
            os.path.join(root, "assets", "Kawaii Analytics Sprout Mascot.png"),
        ]
        import sys
        if hasattr(sys, "_MEIPASS"):
            candidates.insert(0, os.path.join(sys._MEIPASS, "assets", "Cute Sprout Mascot Growth Chart.png"))
            candidates.insert(1, os.path.join(sys._MEIPASS, "seo_audit_pro", "assets", "mascot.png"))
        for cand in candidates:
            if os.path.isfile(cand):
                img = QImage(cand)
                if not img.isNull():
                    _CACHED_MASCOT = img
                    break
    return _CACHED_MASCOT


def logo_image(size):
    mascot = _get_mascot_image()
    if mascot is not None:
        return mascot.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
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
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(root, "assets", "Kawaii-Analytics-Sprout-Mascot.ico"),
        _ICO_PATH,
        os.path.join(root, "assets", "icon.ico"),
    ]
    import sys
    if hasattr(sys, "_MEIPASS"):
        candidates.insert(0, os.path.join(sys._MEIPASS, "assets", "Kawaii-Analytics-Sprout-Mascot.ico"))
        candidates.insert(1, os.path.join(sys._MEIPASS, "seo_audit_pro", "assets", "app.ico"))
    for cand in candidates:
        if os.path.isfile(cand):
            ic = QIcon(cand)
            if not ic.isNull():
                return ic
    ic = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        ic.addPixmap(QPixmap.fromImage(logo_image(s)))
    return ic


def write_ico(path, sizes=(16, 20, 24, 32, 40, 48, 64, 128, 256)):
    """Multi-resolution .ico with PNG-compressed entries (Vista+ format)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(root, "assets", "Kawaii-Analytics-Sprout-Mascot.ico"),
        os.path.join(root, "assets", "icon.ico"),
    ]
    for cand in candidates:
        if os.path.isfile(cand) and os.path.abspath(path) != os.path.abspath(cand):
            import shutil
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copyfile(cand, path)
            return
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
