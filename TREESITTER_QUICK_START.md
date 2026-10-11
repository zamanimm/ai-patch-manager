# Tree-Sitter Integration — Quick Start Guide

## The Evolution

### Before (Regex-Only)

```xml
<target>
    <regex>def calculateControl\(([^)]*)\):</regex>
</target>
```

**Problems:**
- ❌ Regex can't handle nested braces
- ❌ Doesn't work well for MATLAB `end`
- ❌ Can't distinguish overloaded functions
- ❌ Requires complex patterns for simple constructs

### After (Tree-Sitter Primary)

```xml
<target>
    <symbol>
        <type>function</type>
        <name>calculateControl</name>
    </symbol>
</target>
```

**Benefits:**
- ✅ Finds exact function boundaries
- ✅ Handles MATLAB `end` correctly
- ✅ Supports methods, classes, structs
- ✅ Disambiguates overloads with `<class>` or `<parameters>`
- ✅ Works for 50+ languages via Tree-Sitter

---

## What This Means for Users

### Patch Targeting Is More Reliable

```
User task: "Rename calculateControl to newController in controller.m"

Before:
  LLM: "I'll generate a regex... hope it doesn't miss the nested if...end"
  App: "Matches found: 0" ❌ (regex too complex)

After:
  LLM: "I'll use structural targeting"
  ```xml
  <symbol>
      <type>function</type>
      <name>calculateControl</name>
  </symbol>
  ```
  App: "Found 1 function. Replacing." ✅
```

---

## What This Means for Developers

### Modular Architecture

```
TreeSitterEngine (core/tree_sitter_engine.py)
    ↓
Language detection + parsing
    ↓
LanguageAdapters (core/language_adapters.py)
    ↓
Symbol resolution (Python, MATLAB, JS, Java, C++, ...)
    ↓
SymbolResolver (core/symbol_resolver.py)
    ↓
Existing Patch Engine (unchanged)
```

### Adding Support for a New Language

1. Create a new Adapter class:

```python
class RustAdapter(LanguageAdapter):
    def find_symbol(self, tree, symbol_type, name, class_name=None, parameters=None):
        # Use Tree-Sitter queries to find functions, traits, methods, etc.
        pass
```

2. Register in factory:

```python
def get_adapter(language: str) -> Optional[LanguageAdapter]:
    adapters = {
        "python": PythonAdapter(),
        "rust": RustAdapter(),  # NEW
        ...
    }
```

3. Done. No changes to parser or validator.

---

## Implementation Timeline

| Phase | Work | Duration | Impact |
|-------|------|----------|--------|
| **1** | Core Tree-Sitter engine | Week 1 | Infrastructure ready |
| **2** | Language adapters (6-8) | Week 2 | Symbol resolution works |
| **3** | Integration + testing | Week 3 | Feature complete |
| **4** | Docs + polish | Week 4 | Production ready |

---

## Backward Compatibility: 100%

All existing patches continue to work:

| Mode | Status |
|------|--------|
| `<match>` exact text | ✅ Still supported |
| `<line_start>/<line_end>` | ✅ Still supported |
| `<regex>` | ✅ Still supported |
| `<symbol>` NEW | ✅ Adds power |

No existing patches break. New patches can use the more powerful symbol mode.

---

## Key Design Principle

```
              LLM
              ↓
         What to change?
              ↓
         "Rename calculateControl"
              ↓
         <symbol><name>calculateControl</name></symbol>
              ↓
         Tree-Sitter + Adapter
              ↓
         Where is it?
              ↓
         byte range: [1024, 1156]
              ↓
         Patch Engine
              ↓
         How to apply?
              ↓
         Replace source[1024:1156]
```

**Separation of concerns:**
- LLM = WHAT
- Tree-Sitter = WHERE
- Patch Engine = HOW

---

## Supported Languages (Phase 2+)

### Tier 1 (Core, Fully Tested)
- Python
- JavaScript / TypeScript
- Java
- C / C++
- MATLAB

### Tier 2 (Well-Maintained, Tested)
- C#
- Go
- Rust
- Ruby
- PHP

### Tier 3 (Available, Can Be Supported)
- Swift
- Kotlin
- Dart
- Lua
- Julia
- Bash / Shell
- PowerShell
- HTML / CSS
- JSON / YAML / TOML / XML
- SQL
- CMake / Makefile / Dockerfile

All via `tree-sitter-languages` package — no custom parsers needed.

---

## Example: MATLAB Function with Nested `if`

### The Problem (Regex)

```matlab
function result = calculateControl(x)
    if x > 0
        result = x * 2;
    end
    
    return;
end
```

Regex `function\s+result\s*=\s*calculateControl.*?end` is **ambiguous:**
- First `end` terminates the `if` block
- Second `end` terminates the function
- Regex takes the first `end` → **Wrong span!**

### The Solution (Tree-Sitter)

```xml
<target>
    <symbol>
        <type>function</type>
        <name>calculateControl</name>
    </symbol>
</target>
```

MATLABAdapter uses Tree-Sitter grammar to:
1. Find `function calculateControl(x)`
2. Traverse to find matching `end` for this function (not nested `if`'s)
3. Return exact span = function start to function-closing `end`

**Result:** Always correct, even with nested structures.

---

## Configuration & Requirements

### Add to `requirements.txt`

```
tree-sitter>=0.20.0
tree-sitter-languages>=1.10.0
```

### Offline Support

Tree-Sitter grammars are cached locally. After installation:

```bash
pip install tree-sitter tree-sitter-languages
```

The app works fully offline. No internet connection needed for normal operation.

---

## Next Steps

1. **Review** `TREESITTER_IMPLEMENTATION.md` for full technical plan
2. **Discuss** which languages to support in Phase 2
3. **Implement** Phase 1 (core infrastructure)
4. **Test** with Python, MATLAB, JavaScript
5. **Extend** to other languages
6. **Document** each adapter

---

## Questions?

- **Why Tree-Sitter vs. Language-Specific Parsers?**
  Single unified interface, 50+ languages out of the box, well-maintained.

- **Will existing patches break?**
  No. Full backward compatibility. Old modes still work.

- **Performance?**
  Excellent. Tree-Sitter is fast and designed for editors (sub-100ms for most files).

- **Offline capability?**
  Yes. Grammars are cached. No internet required for normal operations.

- **How to add a new language?**
  Create an Adapter (20-50 lines), register in factory. Done.
