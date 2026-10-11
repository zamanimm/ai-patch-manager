# AI Patch Manager — Architecture & Design Philosophy

## Overview

AI-Patch-Manager uses a **layered targeting system** with Tree-Sitter as the primary structural resolver:

```
LLM (What to change?)
    ↓
Symbol / Structural Target (NEW - Tree-Sitter)
Exact Match (safest - small code blocks)
Line Range (when line numbers known)
Regex (fallback - simple patterns)
    ↓
Application (validates, verifies hash, shows diff)
    ↓
User (approves or rejects)
    ↓
Patch Engine (applies deterministically)
```

## Core Design Principle

**Use the LLM for understanding code and generating precise targets; use AI-Patch-Manager for deterministic matching, validation, preview, approval, and application. Use Tree-Sitter for language-independent structural parsing.**

---

## Why Regex-Based Targeting (Not AST/Tree-sitter)

### 1. The LLM Already Knows the Intended Code

AI-Patch-Manager is an **LLM-assisted code modification system**. The LLM receives the relevant source code *before* proposing a change, so it already has full context.

**Example:** If the user asks "Rewrite the `calculate_total` function to support discounts," the LLM does not need a parser to understand what to target. It has seen the function and can generate a precise regex pattern to identify it.

```python
# LLM has seen this:
def calculate_total(items):
    total = 0
    for item in items:
        total += item.price
    return total

# LLM generates this pattern:
def calculate_total\(([^)]*)\):
```

### 2. Regex Is Sufficient for the Intended Workflow

AI-Patch-Manager's job is **narrow and specific**:

1. Locate the intended source region (regex)
2. Extract that region
3. Verify that the source has not unexpectedly changed (hash)
4. Show the proposed replacement (diff)
5. Let the user approve or reject it

This is fundamentally different from implementing a compiler or IDE. Regex is sufficient for this scope.

### 3. Why NOT Tree-sitter or Full AST Parsing?

Tree-sitter and AST approaches introduce:

- ❌ Language-specific grammars (maintenance burden)
- ❌ Parser dependencies and version compatibility
- ❌ Language detection logic
- ❌ Scope resolution complexity
- ❌ Heavier deployment footprint
- ❌ Less LLM-friendly (LLMs are better at text patterns than AST manipulation)

AI-Patch-Manager prioritizes:

- ✅ Lightweight
- ✅ Language-independent
- ✅ LLM-independent
- ✅ Easy to install and maintain
- ✅ Easy to extend

---

## Targeting Hierarchy

### Priority 1: Exact Match

For small, unique pieces of code:

```xml
<target>
    <match><![CDATA[total += item.price;]]></match>
</target>
```

**When to use:** Single short statement, unique in the file, low risk of false matches.

### Priority 2: Line Range

When you know exact line numbers:

```xml
<target>
    <line_start>10</line_start>
    <line_end>15</line_end>
    <hash>sha256_of_original</hash>
</target>
```

**When to use:** Precise line ranges are known; hash provides verification.

### Priority 3: Regex Pattern

For structural/semantic refactoring:

```xml
<target>
    <regex>def calculate_total\(([^)]*)\):</regex>
</target>
```

**When to use:**
- Function/method renames
- Signature changes
- Decorators, imports, parameters
- Replace-all structural patterns

**NOT for:**
- Complex logic (use Exact Match instead)
- Function bodies (regex can't reliably match nested braces)
- Highly context-dependent code

---

## Safety Model: The Four-Step Process

```
Source Code (file in project)
    ↓
LLM sees source + understands user request
    ↓
LLM generates precise target pattern (exact / regex / line range)
    ↓
AI-Patch-Manager VALIDATES:
  • 0 matches     → REJECT
  • 1 match       → CONTINUE
  • 2+ matches    → REJECT (ambiguous)
    ↓
AI-Patch-Manager VERIFIES:
  • Hash of matched region matches expected (if provided)
  • Generates unified diff
    ↓
User APPROVES in the UI
    ↓
AI-Patch-Manager APPLIES:
  • Creates backup snapshot
  • Modifies file
  • Logs transaction
```

**The fundamental rule:**
> LLM proposes. Application validates. User approves. Application applies.

---

## Language Coverage

### Python

Uses indentation to define blocks. Regex can reliably identify functions, classes, methods:

```regex
def calculate_total\([^)]*\):
```

### MATLAB

Uses `function` and `end`. Clear textual boundaries:

```regex
function\s+result\s*=\s*calculateTotal\([^)]*\)
```

### C/C++/C#/Java/JavaScript

Brace-based languages with clear structural patterns:

```regex
(public\s+)?(static\s+)?double\s+calculateTotal\([^)]*\)\s*\{
```

### JavaScript/TypeScript

Arrow functions, class methods, async/await:

```regex
(async\s+)?(const|function)\s+calculateTotal\s*=\s*\([^)]*\)\s*=>
```

**Key insight:** The LLM can generate a pattern specific to the source it received. It does not have to be a universal parser — it just needs to uniquely identify the region in *this specific file*.

---

## Anti-Patterns: What NOT To Do

### ❌ DO NOT: Rely on First Match

```python
# WRONG: Takes first match even if multiple exist
match = pattern.search(file_text)
if match:
    apply_change()  # ← DANGER: What if there are 2+ matches?
```

### ✅ DO: Strict Validation

```python
# RIGHT: Rejects if ambiguous
matches = list(pattern.finditer(file_text))
if len(matches) == 1:
    apply_change()
else:
    reject(f"{len(matches)} matches found (expected 1)")
```

### ❌ DO NOT: Skip Hash Verification

```python
# WRONG: No way to detect if file changed after LLM generated proposal
matched_text = extract(file_text)
apply_replacement()
```

### ✅ DO: Verify Hash

```python
# RIGHT: Detects unexpected changes
matched_text = extract(file_text)
if sha256(matched_text) != expected_hash:
    reject("File changed after prompt was generated")
apply_replacement()
```

---

## Failure Modes: When to Reject

| Condition | Action |
|-----------|--------|
| 0 matches | Reject: "target not found" |
| 2+ matches | Reject: "ambiguous (multiple matches)" |
| Hash mismatch | Reject: "CONFLICT: file changed" |
| Regex syntax error | Reject: "invalid pattern" |
| Target is empty | Reject: "no target provided" |

The system should **never silently guess**. Rejection is explicit.

---

## Limitations

Regex-based targeting will not work well for:

- **Nested language constructs** (regex can't match balanced braces reliably)
- **Complex preprocessor macros** (language-specific, hard to generalize)
- **Unusual formatting** (unexpected whitespace, comments)
- **Generated code** (often has patterns that break assumptions)
- **Ambiguous repeated functions** (same signature in multiple places)

**For these cases:** Use Exact Match mode (`<match>`) instead. Have the LLM provide the exact code block to replace.

---

## Future Extensibility

This architecture does NOT preclude adding semantic analysis later:

1. **Phase 1 (current):** Regex + exact match + line ranges
2. **Phase 2 (optional):** Add Tree-sitter for selected languages (Python, JavaScript)
3. **Phase 3 (optional):** Add language-specific scope resolvers

The regex-first approach keeps the core lightweight while remaining open to extension.

---

## Summary

| Aspect | Design Decision |
|--------|-----------------|
| **Parsing approach** | LLM generates patterns; app validates deterministically |
| **Targeting modes** | Exact match (priority 1), line range (2), regex (3) |
| **Safety** | Hash verification + strict match count validation |
| **Language support** | Language-agnostic via LLM-generated patterns |
| **Dependencies** | Minimal (Python `re` module only) |
| **Failure handling** | Explicit rejection; never silent guessing |
| **Extensibility** | Open to AST/Tree-sitter in future phases |

The design is **intentionally conservative:** prefer rejection and user feedback over silent incorrect modifications.
