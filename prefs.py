# -*- coding: utf-8 -*-
"""
Persistence for keys and options, so they're entered once and remembered.

Two stores, both living next to the tool (and next to the .exe when frozen):
  * ``.env``        — API keys (loaded automatically at startup by config.py).
  * ``settings.json`` — non-secret preferences (provider, model, page count,
                        PageSpeed strategy, single-page, auto-open, proxy).

Keys are stored locally in plaintext in the tool's own ``.env`` — the same
approach the other Mindcob tools use — and ``.env`` is git-ignored. Nothing is
sent anywhere.
"""

import json
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _base_dir():
    # When frozen into a standalone .exe, persist next to the executable (that's
    # where a recipient's .env lives); otherwise next to this module.
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return APP_DIR


def env_path():
    return os.path.join(_base_dir(), ".env")


def settings_path():
    return os.path.join(_base_dir(), "settings.json")


def save_env(mapping):
    """Merge KEY=VALUE pairs into the tool's .env, preserving every other line,
    and update os.environ so the change takes effect immediately. Empty values
    are written too, so clearing a field in the UI clears the stored key.
    Returns the .env path (never raises)."""
    path = env_path()
    lines = []
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            lines = []
    done = set()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("export "):
            s = s[len("export "):]
        for k, v in mapping.items():
            if s.startswith(k + "="):
                lines[i] = "%s=%s" % (k, v)
                done.add(k)
    for k, v in mapping.items():
        if k not in done:
            lines.append("%s=%s" % (k, v))
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
    except OSError:
        pass
    for k, v in mapping.items():
        os.environ[k] = v
    return path


def apply_saved_env():
    """Make the tool's OWN saved .env authoritative for this process.

    config.load_env() keeps a real OS environment variable over the .env when
    they disagree — sensible for servers, but wrong for a desktop key manager:
    if the user clears a key in the app (writing an empty value to .env), that
    intent must win. This re-applies every key/value found in the tool's .env
    (including empty values) so the app's own store is the source of truth for
    the keys it manages. Only keys present in the .env file are touched. Safe to
    call once at startup; never raises.
    """
    path = env_path()
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                if k:
                    os.environ[k] = v
    except OSError:
        pass


def load_settings():
    """Return the saved preferences dict (empty on any problem)."""
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data):
    """Write non-secret preferences to settings.json (never raises)."""
    try:
        with open(settings_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass
