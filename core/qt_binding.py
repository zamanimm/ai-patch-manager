"""Qt binding selection with a zero-extra-dependency PySide6 path.

PySide6 is loaded directly and does not require QtPy. PyQt6 and PyQt5 use the
real QtPy compatibility layer. PyQt5 is supported as a legacy fallback; the
UI uses QtPy's normalized API and ``exec_qt`` for dialog/event-loop execution.

Preference order is PySide6, PyQt6, then PyQt5 unless QT_API explicitly selects
another installed provider. Selection happens once, before importing UI code.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from types import ModuleType
from dataclasses import dataclass
from typing import Optional, Tuple


_BINDING_MODULES = {
    "pyside6": "PySide6",
    "pyqt6": "PyQt6",
    "pyqt5": "PyQt5",
}
_PREFERENCE = ("pyside6", "pyqt6", "pyqt5")
_QT_MODULES = None
_SELECTED_API: Optional[str] = None


class QtBindingError(ImportError):
    """Raised when no usable supported Qt binding is available."""


@dataclass(frozen=True)
class QtModules:
    api: str
    QtCore: object
    QtGui: object
    QtWidgets: object


def _normalise_api(value: str) -> Optional[str]:
    value = (value or "").strip().lower().replace("-", "").replace("_", "")
    aliases = {
        "pyside6": "pyside6", "pyside": "pyside6",
        "pyqt6": "pyqt6", "pyqt": "pyqt6",
        "pyqt5": "pyqt5",
    }
    return aliases.get(value)


def choose_qt_api(available, requested: Optional[str] = None) -> Optional[str]:
    """Choose a binding from an iterable of API names; pure and easy to test."""
    available = {_normalise_api(item) for item in (available or ())}
    available.discard(None)
    if requested:
        selected = _normalise_api(requested)
        if selected is None:
            raise QtBindingError(
                f"Unsupported QT_API={requested!r}. Use 'pyside6', 'pyqt6', or 'pyqt5'.")
        if selected not in available:
            module = _BINDING_MODULES[selected]
            raise QtBindingError(
                f"QT_API requests {selected}, but {module} is not installed. "
                f"Install it with: python -m pip install -r "
                f"requirements-{selected}.txt")
        return selected
    for candidate in _PREFERENCE:
        if candidate in available:
            return candidate
    return None


def installed_qt_bindings(spec_finder=None) -> Tuple[str, ...]:
    """Return installed supported bindings without importing their Qt libraries."""
    finder = spec_finder or importlib.util.find_spec
    found = []
    for api, module in _BINDING_MODULES.items():
        try:
            if finder(module) is not None:
                found.append(api)
        except (ImportError, ModuleNotFoundError, ValueError):
            continue
    return tuple(found)


def _purge_qtpy_modules() -> None:
    """Clear a failed partial QtPy import before trying the other binding."""
    for name in tuple(sys.modules):
        if name == "qtpy" or name.startswith("qtpy."):
            sys.modules.pop(name, None)


def _find_spec(name: str):
    """find_spec wrapper that tolerates mocked/partially imported modules."""
    if name in sys.modules:
        return getattr(sys.modules[name], "__spec__", None) or True
    try:
        return importlib.util.find_spec(name)
    except (ImportError, ModuleNotFoundError, ValueError):
        return None


def _install_guidance(api: Optional[str] = None, *, binding_detected: bool = False) -> str:
    if api in ("pyqt5", "pyqt6") and binding_detected:
        label = "PyQt5" if api == "pyqt5" else "PyQt6"
        return (
            f"{label} is already installed, but the QtPy compatibility layer is missing.\n\n"
            "Install only the missing compatibility package:\n\n"
            "  python -m pip install qtpy\n\n"
            f"Or install the complete {label} environment with:\n\n"
            f"  python -m pip install -r requirements-{api}.txt"
        )
    if api == "pyqt5":
        return (
            "PyQt5 support uses QtPy for cross-version API compatibility.\n\n"
            "Install the complete PyQt5 option with:\n\n"
            "  python -m pip install -r requirements-pyqt5.txt"
        )
    if api == "pyqt6":
        return (
            "PyQt6 support uses QtPy for Qt 6 enum/API compatibility.\n\n"
            "Install the complete PyQt6 option with:\n\n"
            "  python -m pip install -r requirements-pyqt6.txt\n\n"
            "If PySide6 is already installed, you can use it without QtPy by "
            "removing QT_API=pyqt6 or setting QT_API=pyside6."
        )
    return (
        "No supported Qt binding (PySide6, PyQt6, or PyQt5) was found.\n\n"
        "Install PySide6 (recommended):\n\n"
        "  python -m pip install -r requirements-pyside6.txt\n\n"
        "Or install PyQt6:\n\n"
        "  python -m pip install -r requirements-pyqt6.txt\n\n"
        "Or install PyQt5:\n\n"
        "  python -m pip install -r requirements-pyqt5.txt"
    )


def _register_qtpy_aliases(api: str, modules: dict) -> None:
    """Expose native PySide6 modules under qtpy.* import names.

    The UI already imports from qtpy.*. For PySide6, those modules have the
    same public classes/signals used here, so a tiny in-memory namespace keeps
    the existing imports working without requiring the third-party QtPy wheel.
    This compatibility alias is deliberately only used for PySide6. PyQt6's
    scoped enum and signal names are handled by real QtPy.
    """
    _purge_qtpy_modules()
    package = ModuleType("qtpy")
    package.__package__ = "qtpy"
    package.__path__ = []
    package.__version__ = "builtin-pyside6-compat"
    package.API_NAME = "PySide6"
    package.QT_VERSION = getattr(modules["QtCore"], "qVersion", lambda: "")()
    for submodule, module in modules.items():
        alias = f"qtpy.{submodule}"
        sys.modules[alias] = module
        setattr(package, submodule, module)
    sys.modules["qtpy"] = package


def _load_native_pyside6() -> QtModules:
    """Load PySide6 directly and install import aliases for existing UI code."""
    native = {
        "QtCore": importlib.import_module("PySide6.QtCore"),
        "QtGui": importlib.import_module("PySide6.QtGui"),
        "QtWidgets": importlib.import_module("PySide6.QtWidgets"),
    }
    # These modules are optional for normal startup. Register them only if
    # present, without making application launch depend on QtSvg/QtTest.
    for submodule in ("QtSvg", "QtTest"):
        try:
            native[submodule] = importlib.import_module(f"PySide6.{submodule}")
        except ImportError:
            pass
    _register_qtpy_aliases("pyside6", native)
    return QtModules("pyside6", native["QtCore"], native["QtGui"], native["QtWidgets"])


def _load_via_qtpy(api: str) -> QtModules:
    os.environ["QT_API"] = api
    _purge_qtpy_modules()
    qtpy = importlib.import_module("qtpy")
    core = importlib.import_module("qtpy.QtCore")
    gui = importlib.import_module("qtpy.QtGui")
    widgets = importlib.import_module("qtpy.QtWidgets")
    actual = _normalise_api(getattr(qtpy, "API_NAME", "") or api)
    if actual and actual != api:
        raise RuntimeError(f"QtPy selected {actual}, but {api} was requested")
    return QtModules(api, core, gui, widgets)


def load_qt_modules() -> QtModules:
    """Load one Qt provider.

    PySide6 works without QtPy. PyQt6 and PyQt5 require QtPy. If several
    providers are installed, PySide6 is preferred, then PyQt6, then PyQt5.
    QT_API can explicitly select an installed provider.
    """
    global _QT_MODULES, _SELECTED_API
    if _QT_MODULES is not None:
        return _QT_MODULES

    requested = os.environ.get("QT_API", "").strip()
    installed = installed_qt_bindings()
    if not installed:
        raise QtBindingError(_install_guidance())

    api = choose_qt_api(installed, requested or None)
    qtpy_available = _find_spec("qtpy") is not None

    try:
        if qtpy_available:
            try:
                loaded = _load_via_qtpy(api)
            except Exception:
                # A broken/mismatched QtPy installation must not block a
                # working PySide6 installation. PyQt6/PyQt5 still require QtPy.
                _purge_qtpy_modules()
                if api != "pyside6":
                    raise
                native = _load_native_pyside6()
                loaded = native
        elif api == "pyside6":
            loaded = _load_native_pyside6()
        else:
            # The binding is already present; only QtPy is missing. Tell the
            # user to install the compatibility layer alone, not another Qt.
            raise QtBindingError(_install_guidance(api, binding_detected=True))
    except QtBindingError:
        raise
    except Exception as exc:
        _purge_qtpy_modules()
        hint = _install_guidance(api)
        raise QtBindingError(
            f"{_BINDING_MODULES[api]} was detected but could not be loaded "
            f"({type(exc).__name__}: {exc}).\n\n{hint}"
        ) from exc

    os.environ["QT_API"] = api
    _QT_MODULES = loaded
    _SELECTED_API = api
    return _QT_MODULES


def selected_qt_api() -> Optional[str]:
    """Return the loaded API name, or the environment preference if not loaded."""
    if _SELECTED_API:
        return _SELECTED_API
    return _normalise_api(os.environ.get("QT_API", ""))


def reset_qt_binding_for_tests() -> None:
    """Reset module-level selection for isolated tests only."""
    global _QT_MODULES, _SELECTED_API
    _QT_MODULES = None
    _SELECTED_API = None
    _purge_qtpy_modules()
