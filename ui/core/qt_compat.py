"""Small Qt API compatibility helpers shared by Qt 5 and Qt 6 bindings.

Keep this module free of Qt imports so it is safe to import before provider
selection. PyQt5 exposes ``exec_`` on several objects while Qt 6 bindings expose
``exec``; QtPy does not guarantee the same spelling for every wrapped class.
"""
from __future__ import annotations


def exec_qt(obj, *args, **kwargs):
    """Execute a QApplication, QDialog, or QMenu across Qt 5/6 bindings.

    Prefer the Qt 6 spelling, then fall back to the Qt 5 spelling. This helper
    deliberately does not catch exceptions raised by the selected method.
    """
    method = getattr(obj, "exec", None)
    if not callable(method):
        method = getattr(obj, "exec_", None)
    if not callable(method):
        raise AttributeError(
            f"{type(obj).__name__} provides neither exec() nor exec_()")
    return method(*args, **kwargs)
