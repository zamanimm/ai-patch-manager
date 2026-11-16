# Qt startup compatibility fix

## What was wrong

The previous startup loader required the third-party `qtpy` package before it even
checked whether PySide6 was already installed. As a result, a machine with a working
PySide6 installation could not launch the app unless it installed an extra package.

## New behavior

- Existing PySide6 installations run directly without QtPy.
- The loader registers an in-memory `qtpy.*` namespace pointing at native PySide6
  modules, so the existing UI import paths do not need to be rewritten.
- If PySide6 and PyQt6 are both installed, PySide6 remains the default.
- If PyQt6 is selected, the real QtPy compatibility package is used because PyQt6's
  scoped enum and signal API differs. The PyQt6 requirements bundle includes QtPy.
- A broken QtPy installation does not block a working PySide6 installation; the
  loader falls back to native PySide6.
- Explicit `QT_API` choices are respected. The process never switches bindings after
  Qt modules have begun loading.

## Installation

For a new PySide6 environment:

```powershell
python -m pip install -r requirements-pyside6.txt
```

For an existing PySide6 environment, no QtPy installation is required. If the shared
XML dependency is missing, install it with:

```powershell
python -m pip install lxml
```

For PyQt6:

```powershell
python -m pip install -r requirements-pyqt6.txt
```

## Verification

- Unit tests verify PySide6 selection when QtPy is absent using mocked native Qt modules.
- Unit tests verify that a broken QtPy import falls back to native PySide6.
- Unit tests verify that the PySide6 requirement bundle does not pull QtPy and the
  PyQt6 bundle does.
- GUI tests could not be run in the build environment because neither Qt binding is
  installed. A Windows smoke test with the user's existing PySide6 environment is
  still required.
