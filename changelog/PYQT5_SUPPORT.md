# PyQt5 support

This update adds PyQt5 as a supported fallback provider alongside PySide6 and PyQt6.

## Provider selection

Default preference:

1. PySide6 (direct import; QtPy is not required)
2. PyQt6 (through QtPy)
3. PyQt5 (through QtPy)

Use `QT_API=pyside6`, `QT_API=pyqt6`, or `QT_API=pyqt5` before launch to choose explicitly.

## If only PyQt5 is installed

- If QtPy is also installed, the application selects PyQt5 automatically.
- If QtPy is missing, startup reports that PyQt5 is already installed and asks for only:

  ```powershell
  python -m pip install qtpy
  ```

- A fresh PyQt5 setup can install the complete provider and shared requirements with:

  ```powershell
  python -m pip install -r requirements-pyqt5.txt
  ```

The app does not ask the user to reinstall PyQt5 when only QtPy is missing.

## Qt 5 API compatibility

UI imports continue through `qtpy.*`. Calls to dialog, menu, and application event loops now use `core.qt_compat.exec_qt()`, which invokes `exec()` when available and falls back to `exec_()` for Qt 5 bindings.

## Validation

- Provider-selection and PyQt5-only missing-QtPy guidance tests pass.
- `requirements-pyqt5.txt` is checked for both PyQt5 and QtPy.
- Python compilation and the full non-GUI regression suite pass.
- This build environment has no PyQt5, PyQt6, PySide6, or QtPy runtime installed, so an actual PyQt5 GUI launch could not be executed here. Run the GUI tests in a separate PyQt5 environment before distributing the application.
