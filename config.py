# -*- coding: utf-8 -*-
"""
Tiny .env loader (no dependencies).

Reads KEY=VALUE lines from a ".env" file next to this module (and the current
working directory) into os.environ, so API keys are entered once in a file and
picked up on every launch. Real environment variables always win over the file.

Supported .env syntax:
  KEY=value
  KEY="value with spaces"
  export KEY=value        # leading "export " is ignored
  # comment lines and blank lines are skipped
"""

import os
import sys


def _parse_env_file(path):
    data = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key:
                    data[key] = val
    except OSError:
        pass
    return data


def load_env(override=False):
    """Load .env (module dir, then CWD) into os.environ.

    override=False keeps any variable already set in the real environment.
    Returns the dict of values found. Safe to call more than once.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, ".env"), os.path.join(os.getcwd(), ".env")]
    # When frozen into a standalone .exe (PyInstaller), __file__ points inside
    # the bundle, so also look next to the executable itself — that's where a
    # recipient would drop a .env to persist their keys.
    if getattr(sys, "frozen", False):
        candidates.insert(0, os.path.join(os.path.dirname(sys.executable), ".env"))
    loaded, seen = {}, set()
    for path in candidates:
        ap = os.path.abspath(path)
        if ap in seen or not os.path.isfile(ap):
            continue
        seen.add(ap)
        for k, v in _parse_env_file(ap).items():
            loaded[k] = v
            if override or k not in os.environ:
                os.environ[k] = v
    return loaded
