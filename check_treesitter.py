"""Diagnose Tree-Sitter (symbol targeting) in the current Python environment.

Run with the SAME interpreter that starts the app:
    python check_treesitter.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.tree_sitter_engine import TreeSitterEngine  # noqa: E402

print("interpreter:", sys.executable)
for line in TreeSitterEngine().diagnose():
    print(line)
print()
print("If a language shows FAILED, install what the message suggests, e.g.:")
print("  python -m pip install tree-sitter tree-sitter-python tree-sitter-javascript \\")
print("      tree-sitter-typescript tree-sitter-java tree-sitter-c tree-sitter-cpp")
