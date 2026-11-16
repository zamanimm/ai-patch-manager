"""Symbol resolver using Tree-Sitter for structural code targeting.

Resolves symbol targets (function, class, method, etc.) to exact source ranges
using Tree-Sitter syntax trees and language-specific adapters.
"""
from __future__ import annotations

from typing import Optional, Tuple
from models.change import SymbolTarget
from core.tree_sitter_engine import TreeSitterEngine


class SymbolResolver:
    """Resolve structural targets to exact source ranges via Tree-Sitter."""

    def __init__(self):
        """Initialize resolver with Tree-Sitter engine."""
        self.engine = TreeSitterEngine()
        self._adapters = {}  # Language → adapter cache
        self.last_error = ""      # why the last grammar failed to load
        self.last_language = ""

    def resolve(self, source: str, file_path: str, symbol: SymbolTarget) \
            -> Tuple[Optional[Tuple[int, int]], str]:
        """Resolve a symbol target to an exact source range.

        Args:
            source: Source code as string
            file_path: Path to source file (for language detection)
            symbol: SymbolTarget with type, name, optional class_name, parameters

        Returns:
            Tuple of:
            - (start_line_idx, end_line_idx): zero-based, half-open LINE span
              of the symbol (the unit the validator / patch engine use), or
              None when not found
            - kind: "found" | "not_found" | "ambiguous" | "unsupported_language" |
                    "parse_error" | "unsupported_symbol_type" | "no_tree_sitter" | "no_grammar"
        """

        # Check if Tree-Sitter is available
        if not self.engine.is_available():
            return None, "no_tree_sitter"

        # Detect language from file path
        language = self.engine.detect_language(file_path)
        if not language:
            return None, "unsupported_language"

        # Parse source code
        tree = self.engine.parse(source, language)
        if not tree:
            if language in self.engine.parsers_cache or \
                    self.engine.get_parser(language) is not None:
                return None, "parse_error"
            # language is known but its grammar could not be loaded
            self.last_error = self.engine.errors.get(language, "")
            self.last_language = language
            return None, "no_grammar"

        # Check for parse errors
        if self.engine.has_error_nodes(tree):
            return None, "parse_error"

        # Get language adapter
        adapter = self._get_adapter(language)
        if not adapter:
            return None, "unsupported_language"

        # Find matching symbols
        try:
            matches = adapter.find_symbol(
                tree=tree,
                source=source,
                symbol_type=symbol.type,
                name=symbol.name,
                class_name=symbol.class_name,
                parameters=symbol.parameters
            )
        except Exception:
            return None, "unsupported_symbol_type"

        # Validate match count
        if len(matches) == 0:
            return None, "not_found"
        elif len(matches) > 1:
            return None, "ambiguous"
        else:
            return _bytes_to_line_span(source, matches[0]), "found"

    def _get_adapter(self, language: str):
        """Get the language adapter (None if the language has no adapter)."""
        if language not in self._adapters:
            self._adapters[language] = get_adapter(language)
        return self._adapters[language]


def _bytes_to_line_span(source: str, byte_span: Tuple[int, int]) -> Tuple[int, int]:
    """Convert a UTF-8 byte range to a zero-based half-open line range.

    Region operations always work on WHOLE lines, so a symbol that starts at
    column 4 (e.g. a method) still covers its full first line - the
    replacement must therefore carry the original indentation.
    """
    data = source.encode("utf-8")
    start_b, end_b = byte_span
    start_line = data.count(b"\n", 0, start_b)
    last_byte = max(start_b, end_b - 1)          # last byte INSIDE the node
    end_line = data.count(b"\n", 0, last_byte) + 1
    return (start_line, end_line)


class LanguageAdapter:
    """Base class for language-specific symbol resolution."""

    def find_symbol(self, tree, source: str, symbol_type: str, name: str,
                   class_name: Optional[str] = None,
                   parameters: Optional[list] = None) -> list:
        """Find symbols matching the given criteria.

        Args:
            tree: Tree-Sitter Tree object
            source: Original source code
            symbol_type: "function", "method", "class", "struct", etc.
            name: Symbol name to find
            class_name: For methods, the parent class name (optional)
            parameters: List of parameter names (optional, for disambiguation)

        Returns:
            List of (start_byte, end_byte) tuples for matching symbols
        """
        raise NotImplementedError(
            f"Language adapter {self.__class__.__name__} does not implement find_symbol()"
        )


# ---------------------------------------------------------------------------
# Table-driven adapters
# ---------------------------------------------------------------------------

def _walk(node):
    """Depth-first iteration over a syntax tree (iterative: no recursion limit)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


class _TableAdapter(LanguageAdapter):
    """Generic adapter driven by per-language node-type tables.

    TYPES      symbol type ("function", "class", ...) -> tree-sitter node types
    CONTAINERS node types that can enclose a method (used for <class> filter
               and to tell methods from free functions)
    """

    TYPES: dict = {}
    CONTAINERS: frozenset = frozenset()

    # -- hooks -----------------------------------------------------------------
    def node_name(self, node, src: bytes) -> Optional[str]:
        n = node.child_by_field_name("name")
        return src[n.start_byte:n.end_byte].decode("utf-8", "replace") if n else None

    def candidate_nodes(self, tree):
        return _walk(tree.root_node)

    def span_node(self, node):
        """Node whose byte range is reported (default: the node itself)."""
        return node

    def extra_scope(self, node, src: bytes) -> Optional[str]:
        """Explicit scope declared in the name itself (C++ ``Foo::bar``)."""
        return None

    # -- helpers ---------------------------------------------------------------
    def _enclosing_names(self, node, src: bytes) -> list:
        names = []
        p = node.parent
        while p is not None:
            if p.type in self.CONTAINERS:
                nm = self.node_name(p, src)
                if nm:
                    names.append(nm)
            p = p.parent
        return names

    def _params_ok(self, node, src: bytes, parameters) -> bool:
        if not parameters:
            return True
        pn = node.child_by_field_name("parameters")
        if pn is None:
            decl = node.child_by_field_name("declarator")      # C / C++
            pn = decl.child_by_field_name("parameters") if decl is not None else None
        if pn is None:
            return False
        import re as _re
        words = set(_re.findall(r"[A-Za-z_]\w*", src[pn.start_byte:pn.end_byte].decode("utf-8", "replace")))
        return all(p in words for p in parameters)

    def find_symbol(self, tree, source, symbol_type, name, class_name=None, parameters=None):
        wanted = self.TYPES.get((symbol_type or "").strip().lower())
        if wanted is None:
            raise ValueError(f"unsupported symbol type '{symbol_type}'")
        src = source.encode("utf-8")
        kind = (symbol_type or "").strip().lower()
        results, seen = [], set()
        for node in self.candidate_nodes(tree):
            if node.type not in wanted:
                continue
            if self.node_name(node, src) != name:
                continue
            scopes = self._enclosing_names(node, src)
            extra = self.extra_scope(node, src)
            if extra:
                scopes = [extra] + scopes
            in_container = bool(scopes) or self._in_container(node)
            if kind == "method" and not in_container:
                continue
            if class_name and class_name not in scopes:
                continue
            if not self._params_ok(node, src, parameters):
                continue
            target = self.span_node(node)
            rng = (target.start_byte, target.end_byte)
            if rng not in seen:
                seen.add(rng)
                results.append(rng)
        return results

    def _in_container(self, node) -> bool:
        p = node.parent
        while p is not None:
            if p.type in self.CONTAINERS:
                return True
            p = p.parent
        return False


class PythonAdapter(_TableAdapter):
    """Python: function / method / class.

    The reported range is the ``def`` / ``class`` node itself - decorators
    stay outside the range (keep them out of the replacement, too).
    """
    TYPES = {
        "function": {"function_definition"},
        "method": {"function_definition"},
        "class": {"class_definition"},
    }
    CONTAINERS = frozenset({"class_definition"})


class JavaScriptAdapter(_TableAdapter):
    """JavaScript / TypeScript / TSX: functions (incl. ``const f = () => {}``),
    class methods, classes, interfaces, enums."""
    TYPES = {
        "function": {"function_declaration", "generator_function_declaration",
                     "variable_declarator", "method_definition"},
        "method": {"method_definition", "variable_declarator"},
        "class": {"class_declaration"},
        "interface": {"interface_declaration"},
        "enum": {"enum_declaration"},
    }
    CONTAINERS = frozenset({"class_declaration", "class"})

    _FUNC_VALUES = {"arrow_function", "function_expression", "function",
                    "generator_function"}

    def candidate_nodes(self, tree):
        for n in _walk(tree.root_node):
            if n.type == "variable_declarator":
                val = n.child_by_field_name("value")
                if val is None or val.type not in self._FUNC_VALUES:
                    continue
            yield n

    def span_node(self, node):
        # `const f = () => {}` -> report the whole declaration statement
        if node.type == "variable_declarator":
            p = node.parent
            if p is not None and p.type in ("lexical_declaration", "variable_declaration") \
                    and p.named_child_count == 1:
                return p
        return node


class JavaAdapter(_TableAdapter):
    """Java: methods, constructors, classes, interfaces, enums."""
    TYPES = {
        "function": {"method_declaration", "constructor_declaration"},
        "method": {"method_declaration", "constructor_declaration"},
        "class": {"class_declaration"},
        "interface": {"interface_declaration"},
        "enum": {"enum_declaration"},
    }
    CONTAINERS = frozenset({"class_declaration", "interface_declaration",
                            "enum_declaration"})


class MATLABAdapter(LanguageAdapter):
    """MATLAB symbol resolution via Tree-Sitter.

    MATLAB requires special handling for 'end' statements, which terminate:
    - functions
    - if blocks
    - for loops
    - while loops
    - switch blocks
    - try blocks
    """

    def find_symbol(self, tree, source, symbol_type, name, class_name=None, parameters=None):
        """Find MATLAB functions, nested constructs."""
        # Phase 2: Implement using MATLAB Tree-Sitter grammar
        return []


class CppAdapter(_TableAdapter):
    """C / C++: functions, methods (inline or ``Foo::bar`` out of class),
    classes, structs, enums."""
    TYPES = {
        "function": {"function_definition"},
        "method": {"function_definition"},
        "class": {"class_specifier"},
        "struct": {"struct_specifier"},
        "enum": {"enum_specifier"},
    }
    CONTAINERS = frozenset({"class_specifier", "struct_specifier"})

    def _declarator_name(self, node, src: bytes):
        """Descend declarator chains: pointer_declarator -> function_declarator -> name."""
        d = node.child_by_field_name("declarator")
        while d is not None:
            if d.type in ("identifier", "field_identifier", "type_identifier",
                          "destructor_name", "operator_name"):
                return src[d.start_byte:d.end_byte].decode("utf-8", "replace"), None
            if d.type == "qualified_identifier":
                scope = d.child_by_field_name("scope")
                nm = d.child_by_field_name("name")
                scope_txt = src[scope.start_byte:scope.end_byte].decode("utf-8", "replace") if scope else None
                nm_txt = src[nm.start_byte:nm.end_byte].decode("utf-8", "replace") if nm else None
                return nm_txt, scope_txt
            nxt = d.child_by_field_name("declarator")
            if nxt is None:
                return None, None
            d = nxt
        return None, None

    def node_name(self, node, src: bytes):
        if node.type == "function_definition":
            return self._declarator_name(node, src)[0]
        n = node.child_by_field_name("name")
        # forward declarations (no body) are not symbols we can replace
        if node.type in ("class_specifier", "struct_specifier", "enum_specifier") \
                and node.child_by_field_name("body") is None:
            return None
        return src[n.start_byte:n.end_byte].decode("utf-8", "replace") if n else None

    def extra_scope(self, node, src: bytes):
        if node.type == "function_definition":
            return self._declarator_name(node, src)[1]
        return None


# Adapter factory
def get_adapter(language: str) -> Optional[LanguageAdapter]:
    """Get the adapter for a specific language.

    Args:
        language: Language name (e.g., 'python', 'matlab')

    Returns:
        LanguageAdapter instance or None if not implemented
    """
    adapters = {
        "python": PythonAdapter(),
        "matlab": MATLABAdapter(),
        "javascript": JavaScriptAdapter(),
        "typescript": JavaScriptAdapter(),
        "tsx": JavaScriptAdapter(),
        "java": JavaAdapter(),
        "cpp": CppAdapter(),
        "c": CppAdapter(),
    }
    return adapters.get(language)
