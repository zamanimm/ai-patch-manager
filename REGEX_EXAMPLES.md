# Regex Target Mode — Usage Examples

**📖 See also:** `ARCHITECTURE.md` for the full design philosophy of why AI-Patch-Manager uses regex-based targeting.

The `<regex>` target mode replaces **all occurrences** in a file using Python's `re.sub()`. It is one of three targeting modes; the others are exact `<match>` and line ranges `<line_start>`/`<line_end>`.

## Example 1: Rename a variable (simple)

```xml
<change id="rename-var" path="src/config.py" operation="replace">
    <target>
        <regex>old_config</regex>
    </target>
    <replacement>new_config</replacement>
    <reason>Rename old_config to new_config everywhere</reason>
</change>
```

**File before:**
```python
old_config = load()
result = old_config.get('key')
save(old_config)
```

**File after:**
```python
new_config = load()
result = new_config.get('key')
save(new_config)
```

---

## Example 2: Rename with scope (capture group)

```xml
<change id="rename-scoped" path="api.py" operation="replace">
    <target>
        <regex>(\w+)_handler_old</regex>
    </target>
    <replacement>\1_handler_new</replacement>
    <reason>Rename all *_handler_old functions to *_handler_new</reason>
</change>
```

**File before:**
```python
def get_handler_old():
    pass

def post_handler_old():
    pass
```

**File after:**
```python
def get_handler_new():
    pass

def post_handler_new():
    pass
```

---

## Example 3: Fix imports (multi-line with backreference)

```xml
<change id="fix-import" path="main.py" operation="replace">
    <target>
        <regex>from old_module import (\w+)</regex>
    </target>
    <replacement>from new_module import \1</replacement>
    <reason>Change all imports from old_module to new_module</reason>
</change>
```

**File before:**
```python
from old_module import foo
from old_module import bar
from old_module import baz
```

**File after:**
```python
from new_module import foo
from new_module import bar
from new_module import baz
```

---

## Example 4: Complex pattern (underscore spacing)

```xml
<change id="fix-spacing" path="utils.py" operation="replace">
    <target>
        <regex>(\w+)__(\w+)</regex>
    </target>
    <replacement>\1_\2</replacement>
    <reason>Fix double underscores in variable names</reason>
</change>
```

**File before:**
```python
value__a = 1
result__b = 2
config__name = "test"
```

**File after:**
```python
value_a = 1
result_b = 2
config_name = "test"
```

---

## Important Notes

- **Python `re` syntax** — use standard Python regex patterns
- **Backreferences** — `\1`, `\2`, `\g<name>`, etc.
- **MULTILINE mode** — `^` and `$` match line boundaries, not just string boundaries
- **ONLY VALID PATTERNS** — invalid regex will fail validation with a clear error message
- **REPLACE ALL** — touches **every match** in the file (not just the first)
- **Applied first** — regex changes run before any line-range operations

---

## Semantic Patterns (Structural Refactoring)

Regex excels at **structural** changes — renaming functions, changing signatures, updating imports:

```xml
<!-- Rename function definition AND all calls -->
<change id="refactor-func" path="src/handler.py" operation="replace">
    <target>
        <regex>def old_api\(([^)]*)\):</regex>
    </target>
    <replacement>def new_api(\1):</replacement>
    <reason>Rename API function from old_api to new_api</reason>
</change>
```

**What works:**
- Function/method renames: `def old_func\(` → `def new_func(`
- Signature changes: `func\(a, b\)` → `func(a, b, c=None)`
- Decorator swaps: `@old_decorator` → `@new_decorator`
- Import changes: `from old_lib import` → `from new_lib import`
- Parameter renames: `old_param=` → `new_param=`

**What DOESN'T work with regex:**
- ❌ Replacing function **bodies** (regex can't match balanced braces)
- ❌ Complex control flow refactoring
- ❌ Variable scope analysis
- ❌ Multi-level nested structures

**For complex semantic changes, use `<match>` mode instead** (exact code blocks).

---

## When to use Regex vs. Other Modes

| Mode | Use Case |
|------|----------|
| `<match>` | Complex logic, function bodies, nested structures, anything requiring context |
| `<line_start>/<line_end>` | Fixed line ranges, when you know exact line numbers |
| `<regex>` | Structural refactoring (renames, signatures, imports, decorators), repeated patterns |


---

## Real-World Example: Semantic Function Refactoring

**Scenario:** Rename an API function from `get_user_data` to `fetch_user_profile`, changing all definitions and calls.

### Before

```python
# src/api/user.py
def get_user_data(user_id, include_meta=False):
    """Fetch user information."""
    return db.query(user_id)

# src/handlers/profile.py
result = get_user_data(123)
data = get_user_data(user_id=456, include_meta=True)
```

### LLM Response (Regex Solution)

```xml
<change id="refactor-api-1" path="src/api/user.py" operation="replace">
    <target>
        <regex>def get_user_data\(([^)]*)\):</regex>
    </target>
    <replacement>def fetch_user_profile(\1):</replacement>
    <reason>Rename API function to better reflect that it returns a profile object</reason>
</change>

<change id="refactor-api-2" path="src/handlers/profile.py" operation="replace">
    <target>
        <regex>get_user_data\(([^)]*)\)</regex>
    </target>
    <replacement>fetch_user_profile(\1)</replacement>
    <reason>Update all calls to use the new function name</reason>
</change>
```

### After

```python
# src/api/user.py
def fetch_user_profile(user_id, include_meta=False):
    """Fetch user information."""
    return db.query(user_id)

# src/handlers/profile.py
result = fetch_user_profile(123)
data = fetch_user_profile(user_id=456, include_meta=True)
```

**Why this works:** Regex matches the function structure (the signature), not the logic. Backreferences preserve parameters intact.

---

## When the LLM Should NOT Use Regex

### Example: Complex Logic Changes

**DON'T do this:**
```xml
<!-- ❌ WRONG: Trying to replace function body with regex -->
<change id="bad-example" path="utils.py" operation="replace">
    <target>
        <regex>def process\(data\):.*?return result</regex>
    </target>
    <replacement>def process(data):\n    # new logic here</replacement>
</change>
```

**Problems:**
- Nested braces/brackets confuse regex (DOTALL can't reliably match balanced structures)
- Indentation varies → pattern fails
- Comments/docstrings break the pattern

**DO this instead:**
```xml
<!-- ✅ RIGHT: Use <match> for the exact code block -->
<change id="good-example" path="utils.py" operation="replace">
    <target>
        <match><![CDATA[def process(data):
    x = compute(data)
    y = transform(x)
    return y]]></match>
    </target>
    <replacement><![CDATA[def process(data):
    # Optimized version
    return direct_compute(data)]]></replacement>
    <reason>Replace the function logic with a faster implementation</reason>
</change>
```


---

## Decision Tree: Which Targeting Mode?

When you request a code change, the LLM should choose the targeting mode like this:

```
Is the code block small and unique in the file?
├─ YES → Use <match> (exact text matching)
│         Example: single line, unique statement
└─ NO
   
   Are the exact line numbers known?
   ├─ YES → Use <line_start>/<line_end> (line range)
   │         Example: user specified "lines 10-15"
   └─ NO
      
      Is this a structural/identifier change?
      ├─ YES → Use <regex> (pattern matching)
      │         Examples: rename function, change import, swap decorator
      └─ NO → Use <match> (exact text matching is safer)
              Examples: function body, logic, complex expressions
```

### Example Decisions

| Request | Mode | Why |
|---------|------|-----|
| "Rename function `old_api` to `new_api`" | `<regex>` | Structural; pattern is `def old_api\(` → `def new_api(` |
| "Replace the logic in calculate_total (lines 10-15)" | `<match>` | Complex; regex can't reliably match nested braces |
| "Change all `old_import` to `new_import`" | `<regex>` | Structural; pattern is `old_import` → `new_import` |
| "Replace this exact line: `total += price`" | `<match>` | Unique; safe to use exact text |
| "Update function signature from f(a, b) to f(a, b, c)" | `<regex>` | Structural; pattern is `def f\(a, b\)` → `def f(a, b, c)` |

---

## The Golden Rule

> **LLM proposes. Application validates. User approves. Application applies.**

The application will:
- ✅ Accept if regex matches **exactly once**
- ❌ Reject if regex matches **0 times** (not found)
- ❌ Reject if regex matches **2+ times** (ambiguous)
- ❌ Reject if **hash verification fails** (file changed)

Because of this strict validation, **be precise**. The LLM has already seen the source, so it can generate patterns specific to that source, not generic patterns.

