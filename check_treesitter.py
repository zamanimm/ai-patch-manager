"""Diagnose the optional Tree-Sitter feature in the current Python environment.

Run with the SAME interpreter that starts the app:
    python check_treesitter.py

Tree-Sitter is not required to run AI Patch Manager. Without a working grammar,
Symbol targeting is disabled and exact-match / regex / line targeting remain available.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.tree_sitter_engine import TreeSitterEngine  # noqa: E402

print("interpreter:", sys.executable)
engine = TreeSitterEngine()
for line in engine.diagnose():
    print(line)
print()
if not engine.available_languages():
    print("Symbol targeting is unavailable. The rest of AI Patch Manager works normally.")
    print("To install all supported grammars:")
    print("  python -m pip install -r requirements-symbols.txt")
else:
    print("Symbol targeting is available for:", ", ".join(engine.available_languages()))
    print("Additional optional grammars can be installed separately if added to this build.")
