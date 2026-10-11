# Tree-Sitter Integration Implementation Plan

## Current State Analysis

### Existing Architecture (Regex-First)

**Files involved:**
- `models/change.py` — Target dataclass with: match, hash, line_start, line_end, regex
- `core/parser.py` — Parses XML `<target>` elements into Target objects
- `core/validator.py` — locate() function implements priority: exact → hash → line → regex → fuzzy
- `core/patch_engine.py` — Applies changes using located ranges
- `core/diff_engine.py` — Generates unified diffs

**Current priority order:**
1. Exact match (`<match>`)
2. Hash verification (`<hash>`)
3. Regex pattern (`<regex>`) — replace-all with backreferences
4. Line range (`<line_start>/<line_end>`)
5. Fuzzy match (optional)

**Strengths:**
- ✅ Lightweight (no external parser dependencies)
- ✅ Language-agnostic (works via LLM patterns)
- ✅ Simple to implement and maintain

**Limitations:**
- ❌ Regex can't reliably match nested structures (braces, MATLAB `end`)
- ❌ Requires LLM to generate complex patterns for simple constructs
- ❌ Can't distinguish between multiple functions with same name
- ❌ Difficult to handle method overloading

---

## Proposed Evolution: Tree-Sitter Integration

### New Target Mode: Symbol (Structural)

Add a fourth target mode using Tree-sitter for precise semantic targeting:

```xml
<target>
    <symbol>
        <type>function</type>
        <name>calculateControl</name>
        <class>optional_for_methods</class>
        <parameters>
            <parameter>x</parameter>
            <parameter>ref</parameter>
        </parameters>
    </symbol>
</target>
```

### New Priority Order (With Backward Compatibility)

```
symbol / structural target (NEW - most powerful)
        ↓
exact match (existing)
        ↓
line range (existing)
        ↓
regex (existing - fallback for simple patterns)
```

---

## Files to Modify (Minimal Changes)

### 1. `models/change.py`

**Add SymbolTarget dataclass:**

```python
@dataclass
class SymbolTarget:
    """Structural target using Tree-sitter to locate language constructs."""
    type: str = ""                     # function, method, class, struct, etc.
    name: str = ""                     # symbol name
    class_name: Optional[str] = None   # for methods: parent class
    parameters: List[str] = field(default_factory=list)  # optional parameter signature

@dataclass
class Target:
    # existing fields
    match: Optional[str] = None
    hash: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    regex: Optional[str] = None
    
    # NEW
    symbol: Optional[SymbolTarget] = None
    
    def is_empty(self) -> bool:
        return (self.match is None and self.hash is None
                and self.line_start is None and self.regex is None
                and self.symbol is None)
```

**Lines to modify:** ~15 lines

---

### 2. `core/parser.py`

**Add symbol parsing to `_parse_change()`:**

```python
# In target_el processing, add:
symbol_el = target_el.find("symbol")
if symbol_el is not None:
    st = SymbolTarget()
    st.type = _text_of(symbol_el.find("type")) or ""
    st.name = _text_of(symbol_el.find("name")) or ""
    st.class_name = _text_of(symbol_el.find("class"))
    
    params_el = symbol_el.find("parameters")
    if params_el is not None:
        st.parameters = [
            p.text for p in params_el.findall("parameter")
            if p.text
        ]
    target.symbol = st
```

**Lines to modify:** ~20 lines

---

### 3. `core/validator.py`

**Modify `locate()` function:**

```python
def locate(change: Change, file_text: str, settings, language: str = "") \
    -> Tuple[Optional[Tuple[int, int]], str]:
    """
    Priority:
    1. symbol (structural target via Tree-sitter)
    2. exact match
    3. regex
    4. line range
    5. fuzzy
    """
    # NEW: Check symbol first
    if change.target.symbol:
        span, kind = _resolve_symbol(
            change.target.symbol,
            file_text,
            language
        )
        if span is not None:
            return span, kind
        return None, "symbol_not_found"
    
    # Existing code continues...
```

**Lines to modify:** ~30 lines in locate()

---

## New Files to Create

### 1. `core/tree_sitter_engine.py` (~200 lines)

**Responsibilities:**
- Load Tree-sitter parsers from `tree-sitter-language-pack`
- Detect language from file extension
- Parse source code into syntax tree
- Handle parsing errors gracefully

```python
from typing import Optional, Tuple
import tree_sitter as ts
from tree_sitter_languages import get_language, get_parser

class TreeSitterEngine:
    def __init__(self):
        self.parsers_cache = {}
    
    def detect_language(self, file_path: str) -> Optional[str]:
        """Detect language from file extension."""
        # .py → python, .js → javascript, .m → matlab, etc.
    
    def get_parser(self, language: str):
        """Get cached parser for language."""
        if language not in self.parsers_cache:
            parser = get_parser(language)
            self.parsers_cache[language] = parser
        return self.parsers_cache[language]
    
    def parse(self, source: str, language: str) -> Optional[ts.Tree]:
        """Parse source code. Returns None if language unsupported."""
        try:
            parser = self.get_parser(language)
            return parser.parse(source.encode('utf-8'))
        except Exception:
            return None
    
    def has_errors(self, tree: ts.Tree) -> bool:
        """Check if syntax tree has ERROR nodes."""
        # Traverse tree, look for node.type == "ERROR"
    
    def get_node_text(self, source: str, node) -> str:
        """Extract text of a syntax node."""
        return source[node.start_byte:node.end_byte]
```

---

### 2. `core/language_adapters.py` (~400 lines)

**Responsibilities:**
- Language-specific symbol type mappings
- Symbol query patterns for each language
- Language-specific node traversal logic

```python
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

class LanguageAdapter(ABC):
    """Base adapter for language-specific symbol resolution."""
    
    @abstractmethod
    def find_symbol(self, tree, symbol_type: str, name: str, 
                   class_name: Optional[str] = None,
                   parameters: Optional[List[str]] = None) \
                   -> List[Tuple[int, int]]:
        """Return list of (start_byte, end_byte) for matching symbols."""
        pass


class PythonAdapter(LanguageAdapter):
    """Python-specific symbol resolution using Tree-sitter queries."""
    
    def find_symbol(self, tree, symbol_type, name, class_name=None, parameters=None):
        # Use Tree-sitter queries to find:
        # - function_definition with matching name
        # - class_definition
        # - method inside class
        # etc.
        pass


class MATLABAdapter(LanguageAdapter):
    """MATLAB requires special handling for 'end' statements."""
    
    def find_symbol(self, tree, symbol_type, name, class_name=None, parameters=None):
        # MATLAB 'end' is context-dependent:
        # - end of function
        # - end of if/for/while/switch/try
        # Use MATLAB grammar to determine correct end
        pass


class JavaScriptAdapter(LanguageAdapter):
    """JavaScript/TypeScript with arrow functions, classes, etc."""
    pass


class JavaAdapter(LanguageAdapter):
    """Java with methods, classes, annotations."""
    pass


class CppAdapter(LanguageAdapter):
    """C++ with templates, namespace, etc."""
    pass


# Factory function
def get_adapter(language: str) -> Optional[LanguageAdapter]:
    adapters = {
        "python": PythonAdapter(),
        "matlab": MATLABAdapter(),
        "javascript": JavaScriptAdapter(),
        "typescript": JavaScriptAdapter(),
        "java": JavaAdapter(),
        "cpp": CppAdapter(),
        # ... more adapters
    }
    return adapters.get(language)
```

---

### 3. `core/symbol_resolver.py` (~150 lines)

**Responsibilities:**
- Orchestrate Tree-sitter + language adapters
- Handle multiple matches
- Generate diagnostic messages

```python
from typing import Optional, Tuple, List
from core.tree_sitter_engine import TreeSitterEngine
from core.language_adapters import get_adapter
from models.change import SymbolTarget

class SymbolResolver:
    def __init__(self):
        self.engine = TreeSitterEngine()
    
    def resolve(self, source: str, file_path: str, symbol: SymbolTarget) \
            -> Tuple[Optional[Tuple[int, int]], str]:
        """
        Resolve a symbol to an exact source range.
        
        Returns:
            (span, kind) where:
            - span = (start_byte, end_byte) or None
            - kind = "found" | "not_found" | "ambiguous" | "unsupported_language" | "parse_error"
        """
        
        # 1. Detect language
        language = self.engine.detect_language(file_path)
        if not language:
            return None, "unsupported_language"
        
        # 2. Parse source
        tree = self.engine.parse(source, language)
        if not tree:
            return None, "unsupported_language"
        
        # 3. Check for parse errors
        if self.engine.has_errors(tree):
            return None, "parse_error"
        
        # 4. Get language adapter
        adapter = get_adapter(language)
        if not adapter:
            return None, "unsupported_language"
        
        # 5. Find matching symbols
        matches = adapter.find_symbol(
            tree,
            symbol.type,
            symbol.name,
            symbol.class_name,
            symbol.parameters
        )
        
        if len(matches) == 0:
            return None, "not_found"
        elif len(matches) > 1:
            return None, "ambiguous"
        else:
            return matches[0], "found"
```

---

## Implementation Roadmap

### Phase 1: Core Infrastructure (Week 1)

- [ ] Add `tree-sitter` and `tree-sitter-languages` to `requirements.txt`
- [ ] Create `core/tree_sitter_engine.py` with basic parsing
- [ ] Create `core/symbol_resolver.py` with orchestration
- [ ] Add SymbolTarget to `models/change.py`
- [ ] Modify `core/parser.py` to parse `<symbol>` elements
- [ ] Write tests for parser changes

**Test coverage:**
- Parse valid symbol XML
- Parse malformed symbol XML
- Detect various languages

---

### Phase 2: Language Adapters (Week 2)

- [ ] Create `core/language_adapters.py` base class
- [ ] Implement PythonAdapter
- [ ] Implement MATLABAdapter (critical for `end` handling)
- [ ] Implement JavaScriptAdapter
- [ ] Implement JavaAdapter
- [ ] Implement CppAdapter
- [ ] Add stubs for remaining languages

**Test coverage:**
- Find Python function
- Find Python nested function
- Find Python class + method
- Find MATLAB function with nested if/for
- Find JavaScript function with arrow syntax
- Find Java method in class

---

### Phase 3: Integration (Week 3)

- [ ] Modify `core/validator.py` locate() to call symbol resolver
- [ ] Update validator priority: symbol → exact → line → regex → fuzzy
- [ ] Modify `core/validator.py` to pass language hint
- [ ] Update validation tests
- [ ] Create integration tests

**Test coverage:**
- Symbol found uniquely → apply change
- Symbol not found → reject
- Symbol ambiguous (2+ matches) → reject
- Symbol + hash mismatch → reject
- Existing exact/line/regex modes still work

---

### Phase 4: Documentation & Polish (Week 4)

- [ ] Update `ARCHITECTURE.md` with Tree-sitter section
- [ ] Update XML schema examples with symbol mode
- [ ] Update system prompts for LLMs on when to use symbol mode
- [ ] Create migration guide (backward compatibility verified)
- [ ] Add comprehensive TREESITTER_NOTES.md

---

## Test Suite

### Unit Tests: `test_tree_sitter_engine.py`

```python
def test_python_parse_valid():
    # Parse valid Python code
    pass

def test_python_parse_error_recovery():
    # Parse Python with syntax errors → tree still exists
    pass

def test_matlab_parse_valid():
    # Parse valid MATLAB
    pass

def test_unsupported_language():
    # Try to parse unknown language → None
    pass
```

### Unit Tests: `test_language_adapters.py`

```python
def test_python_find_function():
    # Find def foo(): → correct span
    pass

def test_python_find_nested_function():
    # def outer(): def inner(): → find inner
    pass

def test_python_find_class():
    # class Foo: → correct span
    pass

def test_matlab_find_function():
    # function y = foo(x) ... end → correct span
    pass

def test_matlab_find_function_with_nested_if():
    # function with if...end inside → correct end
    pass

def test_javascript_find_function():
    # const foo = () => {} → correct span
    pass

def test_java_find_method():
    # public void foo() {} → correct span
    pass

def test_cpp_find_function():
    # void foo() {} → correct span
    pass

def test_ambiguous_symbol():
    # Two functions with same name → len(matches) > 1
    pass

def test_missing_symbol():
    # Function doesn't exist → len(matches) == 0
    pass
```

### Integration Tests: `test_symbol_resolution.py`

```python
def test_symbol_resolves_to_exact_span():
    # Symbol → SymbolResolver → correct (start_byte, end_byte)
    pass

def test_symbol_hash_verification():
    # Symbol found but hash mismatch → reject
    pass

def test_symbol_preserves_surrounding_code():
    # Replace function → other code in file unchanged
    pass

def test_backward_compatibility_exact_match():
    # Old-style <match> still works
    pass

def test_backward_compatibility_regex():
    # Old-style <regex> still works
    pass
```

---

## Backward Compatibility Matrix

| Mode | Before | After | Status |
|------|--------|-------|--------|
| `<match>` | Works | Works | ✅ No change |
| `<line_start>/<line_end>` | Works | Works | ✅ No change |
| `<regex>` | Works | Works | ✅ No change |
| `<symbol>` | N/A | NEW | ✅ Additive |

**All existing patches remain valid.**

---

## Error Handling

Diagnostic messages for LLM feedback:

| Condition | Error | Resolution |
|-----------|-------|-----------|
| Language unsupported | "Unsupported language: xyz" | Use exact match instead |
| Parse error | "Syntax error in file (unrecoverable)" | Use exact match; file has syntax errors |
| Symbol not found | "Function foo not found" | Check spelling; try exact match |
| Symbol ambiguous | "Found 2 functions named foo; use class/parameters to disambiguate" | Add `<class>` or `<parameters>` |
| Parser unavailable | "Tree-sitter parser for python not available (offline?)" | Ensure grammars cached |

---

## Requirements.txt Additions

```
tree-sitter>=0.20.0
tree-sitter-languages>=1.10.0
```

These provide 50+ language grammars out of the box, with offline caching support.

---

## Final Design

```
Source Code
    ↓
Determine language (file extension)
    ↓
Tree-Sitter parses → syntax tree
    ↓
Language adapter resolves symbol → exact range
    ↓
Symbol validator (0 matches → reject, 2+ → reject)
    ↓
Existing patch engine (exact match, regex fallback)
    ↓
File modified
```

**Responsibility split:**
- **LLM:** Proposes WHAT (symbol name, type, class)
- **Tree-Sitter + Adapter:** Finds WHERE (exact byte range)
- **Patch Engine:** Applies HOW (modification, backup, logging)

---

## Summary

| Aspect | Current | After |
|--------|---------|-------|
| **Parsing** | Regex-only | Tree-Sitter primary + Regex fallback |
| **Accuracy** | Good for simple patterns | Excellent for all language constructs |
| **Complexity** | Low | Moderate (modular, extensible) |
| **Dependencies** | Minimal | 2 external packages (well-maintained) |
| **Languages** | ~3 well (via LLM patterns) | 50+ languages (via Tree-Sitter) |
| **Backward compat** | N/A | 100% compatible |

This evolution makes AI-Patch-Manager much more reliable for complex code structures while maintaining simplicity for small changes.
