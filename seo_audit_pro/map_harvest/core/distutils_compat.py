"""distutils compatibility shim for Python 3.12+.

undetected-chromedriver imports `from distutils.version import LooseVersion`
and uses `release.version[0]` to read the Chrome major version.
"""

from __future__ import annotations

import re
import sys
import types

_COMPONENT_RE = re.compile(r"(\d+ | [a-z]+ | \.)", re.VERBOSE)


class LooseVersion:
    """Drop-in replacement for distutils.version.LooseVersion."""

    def __init__(self, vstring: str = ""):
        self.vstring = ""
        self.version: list = []
        if vstring:
            self.parse(str(vstring))

    def parse(self, vstring: str) -> None:
        self.vstring = vstring
        components = [
            c for c in _COMPONENT_RE.split(vstring) if c and c != "."
        ]
        for i, part in enumerate(components):
            try:
                components[i] = int(part)
            except ValueError:
                pass
        self.version = components

    def __str__(self) -> str:
        return self.vstring

    def __repr__(self) -> str:
        return f"LooseVersion ('{self.vstring}')"

    def _cmp_key(self, other):
        if isinstance(other, str):
            other = LooseVersion(other)
        if not isinstance(other, LooseVersion):
            return NotImplemented
        return self.version, other.version

    def __lt__(self, other):
        k = self._cmp_key(other)
        return k is not NotImplemented and k[0] < k[1]

    def __le__(self, other):
        k = self._cmp_key(other)
        return k is not NotImplemented and k[0] <= k[1]

    def __gt__(self, other):
        k = self._cmp_key(other)
        return k is not NotImplemented and k[0] > k[1]

    def __ge__(self, other):
        k = self._cmp_key(other)
        return k is not NotImplemented and k[0] >= k[1]

    def __eq__(self, other):
        k = self._cmp_key(other)
        return k is not NotImplemented and k[0] == k[1]

    def __ne__(self, other):
        return not self == other


def _install_shim() -> None:
    if "distutils.version" in sys.modules:
        existing = sys.modules["distutils.version"]
        if hasattr(getattr(existing, "LooseVersion", None), "version"):
            return

    distutils_mod = types.ModuleType("distutils")
    distutils_version_mod = types.ModuleType("distutils.version")
    distutils_version_mod.LooseVersion = LooseVersion
    distutils_mod.version = distutils_version_mod

    sys.modules["distutils"] = distutils_mod
    sys.modules["distutils.version"] = distutils_version_mod


_install_shim()
