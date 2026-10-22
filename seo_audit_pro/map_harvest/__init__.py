# -*- coding: utf-8 -*-
"""Map Harvest integration for SEO Audit Pro."""

import os
import sys

# Ensure PyQt5 calls resolve to PySide6
from PySide6 import QtCore, QtGui, QtWidgets

if not hasattr(QtCore, "pyqtSignal"):
    QtCore.pyqtSignal = QtCore.Signal
if not hasattr(QtCore, "pyqtSlot"):
    QtCore.pyqtSlot = QtCore.Slot

sys.modules.setdefault("PyQt5", sys.modules.get("PySide6"))
sys.modules.setdefault("PyQt5.QtCore", QtCore)
sys.modules.setdefault("PyQt5.QtGui", QtGui)
sys.modules.setdefault("PyQt5.QtWidgets", QtWidgets)

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
