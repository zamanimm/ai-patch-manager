"""Qt 6 binding selection with a zero-extra-dependency PySide6 path.

PySide6 is used directly when installed, even if QtPy is absent. This keeps
existing PySide6 installations working without an additional package. PyQt6
uses QtPy's compatibility aliases because its scoped-enum API differs from the
legacy names used by this UI; the PyQt6 install bundle includes QtPy.

If both bindings are installed, PySide6 is preferred unless QT_API explicitly
selects another provider. Selection happens once, before importing UI modules.
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
}
_PREFERENCE = ("pyside6", "pyqt6")
_QT_MODULES = None
_SELECTED_API: Optional[str] = None


class QtBindingError(ImportError):
    """Raised when no usable Qt 6 binding is available."""


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
                f"Unsupported QT_API={requested!r}. Use 'pyside6' or 'pyqt6'.")
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


def _install_guidance(api: Optional[str] = None) -> str:
    if api == "pyqt6":
        return (
            "PyQt6 support uses QtPy for Qt 6 enum/API compatibility. Install the "
            "small compatibility package and the shared dependencies with:\n\n"
            "  python -m pip install qtpy lxml\n\n"
            "Or install the complete PyQt6 option with:\n\n"
            "  python -m pip install -r requirements-pyqt6.txt\n\n"
            "If PySide6 is already installed, you can use it without QtPy by "
            "removing QT_API=pyqt6 or setting QT_API=pyside6."
        )
    return (
        "No supported Qt 6 binding (PySide6 or PyQt6) was found.\n\n"
        "Install PySide6 (recommended):\n\n"
        "  python -m pip install -r requirements-pyside6.txt\n\n"
        "Or install PyQt6:\n\n"
        "  python -m pip install -r requirements-pyqt6.txt"
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
    """Load one Qt 6 provider.

    Existing PySide6 installations work without QtPy. If PySide6 and PyQt6
    are both installed, PySide6 is preferred. PyQt6 uses QtPy, which is bundled
    in requirements-pyqt6.txt. QT_API can explicitly select either provider.
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
                # working PySide6 installation. PyQt6 still requires QtPy.
                _purge_qtpy_modules()
                if api != "pyside6":
                    raise
                native = _load_native_pyside6()
                loaded = native
        elif api == "pyside6":
            loaded = _load_native_pyside6()
        else:
            raise QtBindingError(_install_guidance("pyqt6"))
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
