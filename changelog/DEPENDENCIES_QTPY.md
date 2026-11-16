# Qt binding and optional dependencies

## Qt 6 provider

The application supports PySide6 and PyQt6. Existing PySide6 installations work without the
third-party QtPy package: `core/qt_binding.py` imports PySide6 directly and registers a small
in-memory `qtpy.*` namespace so the existing UI imports remain unchanged. This means users who
already had PySide6 installed do not need to install QtPy or reinstall their Qt binding.

PyQt6 is supported through the real QtPy compatibility layer because PyQt6 has different
scoped-enum and signal names. The `requirements-pyqt6.txt` bundle includes both PyQt6 and QtPy.

Provider selection:

- PySide6 is preferred when both supported bindings are installed.
- PyQt6 is selected automatically when it is the only installed binding and QtPy is available.
- `QT_API=pyside6` or `QT_API=pyqt6` explicitly selects a provider before launch.
- If both are installed and no preference is set, the existing PySide6 installation is used.
- A failed Qt import is reported; the application never mixes bindings or switches after Qt has
  begun loading.

Install bundles:

```powershell
# PySide6 (QtPy is not installed or required)
python -m pip install -r requirements-pyside6.txt

# PyQt6 (includes QtPy compatibility)
python -m pip install -r requirements-pyqt6.txt
```

`requirements.txt` contains shared non-Qt dependencies only. If PySide6 is already installed,
there is no need to install QtPy. PyQt6 and PySide6 have different licensing terms; review the
applicable license before distributing a closed-source or commercial packaged application.

## Optional Tree-Sitter

Tree-Sitter is not imported by the normal startup path and is not included in `requirements.txt`.
The `Symbol targeting` checkbox is disabled unless the core package and at least one supported
grammar can actually construct a parser.

Install the optional feature with:

```powershell
python -m pip install -r requirements-symbols.txt
```

The app reports which grammars work. With a partial grammar installation, only those languages
are advertised in generated instructions. Other target modes (match, regex, and line ranges)
remain available. A previously saved Symbol preference is retained while the dependency is
missing and becomes active again after the optional packages are installed and the app is restarted.

## Optional syntax highlighting

Pygments is optional. Without it, editors and diff views remain usable as plain text. Install it with:

```powershell
python -m pip install -r requirements-highlighting.txt
```

The two provider files include Pygments by default.

## Verification

Run:

```powershell
python -m unittest discover -s tests -v
python check_treesitter.py
```

GUI tests use the selected Qt provider. Test PySide6 and PyQt6 in separate Python environments;
Qt providers must never be mixed within one process.
