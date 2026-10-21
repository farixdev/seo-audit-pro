# -*- coding: utf-8 -*-
"""
Background execution. All network and file work runs on a thread pool;
results are delivered back on the UI thread. Callbacks are dropped if their
owning widget has been destroyed in the meantime.
"""

import logging
import traceback

import shiboken6
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from seo_audit_pro.core.api import ApiError

log = logging.getLogger("seo_audit_pro.client")
_POOL = QThreadPool.globalInstance()
_POOL.setMaxThreadCount(8)
_LIVE = set()


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn, args, kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()
        self.setAutoDelete(True)

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except ApiError as e:
            self._emit(self.signals.failed, e)
            return
        except Exception as e:  # noqa: BLE001
            log.error("Background task failed: %s\n%s", e, traceback.format_exc())
            self._emit(self.signals.failed, ApiError(-1, "Something went wrong. We couldn't complete this operation.",
                                                     "client_error"))
            return
        self._emit(self.signals.done, result)

    @staticmethod
    def _emit(signal, value):
        try:
            signal.emit(value)
        except RuntimeError:  # the app is shutting down and the receiver is gone
            pass


def run(fn, *args, on_done=None, on_error=None, owner=None, **kwargs):
    """Run ``fn(*args, **kwargs)`` in the background.

    ``on_done(result)`` / ``on_error(ApiError)`` run on the UI thread, and are
    skipped if ``owner`` (a QObject) no longer exists.
    """
    task = _Task(fn, args, kwargs)
    sig = task.signals
    _LIVE.add(sig)

    def _alive():
        return owner is None or shiboken6.isValid(owner)

    def _done(res):
        _LIVE.discard(sig)
        if on_done and _alive():
            on_done(res)

    def _fail(err):
        _LIVE.discard(sig)
        if on_error and _alive():
            on_error(err)

    sig.done.connect(_done)
    sig.failed.connect(_fail)
    _POOL.start(task)
    return sig
