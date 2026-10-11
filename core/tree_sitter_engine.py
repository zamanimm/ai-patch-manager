"""Tree-Sitter parsing engine for structural code analysis.

Provides unified interface to Tree-Sitter language grammars for syntax tree
generation and error detection.
"""
from __future__ import annotations

from typing import Optional
import os


class TreeSitterEngine:
    """Unified Tree-Sitter interface with language detection and caching."""

    # Language detection: file extension → language name
    EXTENSION_MAP = {
        # Python
        ".py": "python",
        
        # JavaScript/TypeScript
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "javascript",
        
        # Java
        ".java": "java",
        
        # C/C++/C#
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".hpp": "cpp",
        ".cs": "c_sharp",
        
        # Go
        ".go": "go",
        
        # Rust
        ".rs": "rust",
        
        # MATLAB
        ".m": "matlab",
        
        # Ruby
        ".rb": "ruby",
        
        # PHP
        ".php": "php",
        
        # Swift
        ".swift": "swift",
        
        # Kotlin
        ".kt": "kotlin",
        
        # Bash/Shell
        ".sh": "bash",
        ".bash": "bash",
        
        # HTML/CSS/JSON/YAML/XML
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".xml": "xml",
        ".toml": "toml",
        
        # Markdown/SQL
        ".md": "markdown",
        ".sql": "sql",
        
        # Dockerfile/CMake/Makefile
        "Dockerfile": "dockerfile",
        "dockerfile": "dockerfile",
        "CMakeLists.txt": "cmake",
        ".cmake": "cmake",
        "Makefile": "make",
        "makefile": "make",
    }

    def __init__(self):
        """Initialize the engine with cached parsers."""
        self.parsers_cache = {}
        self.errors = {}          # language -> why no parser could be built
        self._tree_sitter_available = False
        self._check_tree_sitter()

    def _check_tree_sitter(self) -> bool:
        """Tree-Sitter is usable when the core package imports; grammars come
        from tree_sitter_languages (tree-sitter<0.22) or, as a fallback for
        newer tree-sitter, from per-language packages (tree_sitter_python...)."""
        try:
            import tree_sitter  # noqa: F401
            self._tree_sitter_available = True
            return True
        except ImportError:
            self._tree_sitter_available = False
            return False

    def is_available(self) -> bool:
        """Return True if Tree-Sitter is installed and available."""
        return self._tree_sitter_available

    def detect_language(self, file_path: str) -> Optional[str]:
        """Detect language from file path.
        
        Args:
            file_path: Path to source file (can be relative or absolute)
            
        Returns:
            Language name (e.g., 'python', 'javascript') or None if unknown
        """
        if not file_path:
            return None
        
        # Get just the filename for special cases (Dockerfile, Makefile)
        filename = os.path.basename(file_path)
        if filename in self.EXTENSION_MAP:
            return self.EXTENSION_MAP[filename]
        
        # Try file extension
        for ext, lang in self.EXTENSION_MAP.items():
            if file_path.lower().endswith(ext):
                return lang
        
        return None

    def get_parser(self, language: str):
        """Get cached parser for a language.
        
        Args:
            language: Language name (e.g., 'python', 'javascript')
            
        Returns:
            Parser instance or None if language not available
        """
        if not self._tree_sitter_available:
            return None
        
        if language in self.parsers_cache:
            return self.parsers_cache[language]
        
        reasons = []
        # 1) per-language packages (tree-sitter >= 0.22, any Python version)
        parser = self._modern_parser(language, reasons)
        # 2) legacy bundle (tree-sitter < 0.22 + tree_sitter_languages)
        if parser is None:
            parser = self._legacy_parser(language, reasons)
        if parser is not None:
            self.parsers_cache[language] = parser
            self.errors.pop(language, None)
        else:
            self.errors[language] = "; ".join(reasons) or "no grammar available"
        return parser

    # language -> (python module, function returning the grammar pointer, pip name)
    _MODERN_GRAMMARS = {
        "python": ("tree_sitter_python", "language", "tree-sitter-python"),
        "javascript": ("tree_sitter_javascript", "language", "tree-sitter-javascript"),
        "typescript": ("tree_sitter_typescript", "language_typescript", "tree-sitter-typescript"),
        "tsx": ("tree_sitter_typescript", "language_tsx", "tree-sitter-typescript"),
        "java": ("tree_sitter_java", "language", "tree-sitter-java"),
        "c": ("tree_sitter_c", "language", "tree-sitter-c"),
        "cpp": ("tree_sitter_cpp", "language", "tree-sitter-cpp"),
    }

    @classmethod
    def pip_package_for(cls, language: str) -> Optional[str]:
        spec = cls._MODERN_GRAMMARS.get(language)
        return spec[2] if spec else None

    def _modern_parser(self, language: str, reasons: list):
        spec = self._MODERN_GRAMMARS.get(language)
        if not spec:
            reasons.append(f"no grammar mapping for '{language}'")
            return None
        try:
            import importlib
            from tree_sitter import Language, Parser
            mod = importlib.import_module(spec[0])
            return Parser(Language(getattr(mod, spec[1])()))
        except Exception as exc:
            reasons.append(f"{spec[2]}: {type(exc).__name__}: {exc}")
            return None

    def _legacy_parser(self, language: str, reasons: list):
        try:
            from tree_sitter_languages import get_parser
            return get_parser(language)
        except Exception as exc:
            reasons.append(f"tree_sitter_languages: {type(exc).__name__}: {exc}")
            return None

    def diagnose(self) -> list:
        """Human-readable report of what is installed and what works."""
        import sys
        out = [f"python {sys.version.split()[0]}"]
        try:
            import importlib.metadata as md
            for pkg in ("tree-sitter", "tree-sitter-languages"):
                try:
                    out.append(f"{pkg} {md.version(pkg)}")
                except Exception:
                    out.append(f"{pkg}: not installed")
        except Exception:
            pass
        if not self._tree_sitter_available:
            out.append("tree_sitter cannot be imported")
            return out
        for lang in ("python", "javascript", "typescript", "tsx", "java", "c", "cpp"):
            self.parsers_cache.pop(lang, None)
            ok = self.get_parser(lang) is not None
            out.append(f"  {lang:11} {'OK' if ok else 'FAILED  -> ' + self.errors.get(lang, '')}")
        return out

    def parse(self, source: str, language: str):
        """Parse source code into a syntax tree.
        
        Args:
            source: Source code as string
            language: Language name (e.g., 'python')
            
        Returns:
            Tree-Sitter Tree object or None if parsing fails/language unsupported
        """
        if not self._tree_sitter_available:
            return None
        
        parser = self.get_parser(language)
        if not parser:
            return None
        
        try:
            return parser.parse(source.encode('utf-8'))
        except Exception:
            return None

    def has_error_nodes(self, tree) -> bool:
        """Check if syntax tree contains ERROR nodes.
        
        Tree-Sitter can parse partially-valid code, but ERROR nodes indicate
        unrecoverable parsing issues.
        
        Args:
            tree: Tree-Sitter Tree object
            
        Returns:
            True if ERROR nodes found, False otherwise
        """
        if not tree or not hasattr(tree, 'root_node'):
            return False
        root = tree.root_node
        # `has_error` also covers MISSING nodes and error recovery, which a
        # plain search for ERROR nodes misses (e.g. ``def f(:``)
        if hasattr(root, "has_error"):
            return bool(root.has_error)
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "ERROR" or getattr(node, "is_missing", False):
                return True
            stack.extend(node.children)
        return False

    def get_node_text(self, source: str, node) -> str:
        """Extract text of a syntax node.
        
        Args:
            source: Original source code
            node: Tree-Sitter Node object
            
        Returns:
            Source text for this node
        """
        if not node or not hasattr(node, 'start_byte') or not hasattr(node, 'end_byte'):
            return ""
        
        return source[node.start_byte:node.end_byte]

    def get_node_range(self, node) -> tuple:
        """Get byte range of a node.
        
        Args:
            node: Tree-Sitter Node object
            
        Returns:
            Tuple of (start_byte, end_byte)
        """
        if not node:
            return None
        
        if not hasattr(node, 'start_byte') or not hasattr(node, 'end_byte'):
            return None
        
        return (node.start_byte, node.end_byte)
