# -*- coding: utf-8 -*-
"""Regenerate the desktop client's brand assets from vector source.

  .venv/Scripts/python.exe scripts/build_assets.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QGuiApplication  # noqa: E402

from seo_audit_pro import brand, icons  # noqa: E402

ASSETS = os.path.join(ROOT, "seo_audit_pro", "assets")


def main():
    # A QGuiApplication must exist for SVG rendering; keep a reference while drawing.
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    os.makedirs(ASSETS, exist_ok=True)
    brand.write_ico(os.path.join(ASSETS, "app.ico"))
    with open(os.path.join(ASSETS, "logo.svg"), "w", encoding="utf-8") as fh:
        fh.write(brand.logo_svg())
    with open(os.path.join(ASSETS, "chevron-down.svg"), "w", encoding="utf-8") as fh:
        fh.write(icons.svg_markup("chevron_down", "#71717A", 2))
    with open(os.path.join(ASSETS, "chevron-up.svg"), "w", encoding="utf-8") as fh:
        fh.write(icons.svg_markup("chevron_up", "#71717A", 2))
    with open(os.path.join(ASSETS, "check-white.svg"), "w", encoding="utf-8") as fh:
        fh.write(icons.svg_markup("check", "#FFFFFF", 3))
    brand.logo_image(512).save(os.path.join(ASSETS, "logo-512.png"))
    print("Assets written to", ASSETS)


if __name__ == "__main__":
    main()
