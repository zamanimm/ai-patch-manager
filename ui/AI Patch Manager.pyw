"""Double-click launcher for Windows.

A .pyw file is run by pythonw.exe, which has no console: the app opens with no terminal
window behind it. (Errors are shown in a dialog and saved to ~/.ai-patch-manager/crash.log.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import main  # noqa: E402

raise SystemExit(main())
