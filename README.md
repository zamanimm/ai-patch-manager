# AI Patch Manager

**LLM proposes · app validates · you approve.**

Project documentation and historical release notes are organized under [docs/](docs/README.md).

AI Patch Manager lets you change your code with *any* LLM (ChatGPT, Claude, Gemini, a local model…) without
copy-pasting code back and forth. It builds a prompt from your files, you paste it into the LLM, paste the
answer back, and the app shows an exact diff of every change. Nothing touches your files until you approve it,
and every apply can be undone.

```
Project tab ─► Prompt tab ─► (paste into LLM) ─► Response tab ─► Changes tab ─► Apply ─► History (undo)
```

---

## Install and run

Install one supported Qt provider. **PySide6 is the default recommendation**:

```powershell
python -m pip install -r requirements-pyside6.txt
python main.py
```

If you already use PyQt6, install the equivalent PyQt6 environment instead:

```powershell
python -m pip install -r requirements-pyqt6.txt
python main.py
```

If you only have PyQt5, it is also supported through QtPy:

```powershell
python -m pip install -r requirements-pyqt5.txt
python main.py
```

`requirements.txt` contains only shared non-Qt dependencies (`lxml`). If you already have
PySide6 installed, the app uses it directly: **QtPy is not required**, and you do not need to
reinstall PySide6 or add another compatibility package. The startup loader provides the small
`qtpy.*` import namespace internally for the PySide6 path.

If you already have PySide6, PyQt6, or PyQt5 installed, install only the shared dependencies if needed:

```powershell
python -m pip install -r requirements.txt
```

Provider preference is PySide6, then PyQt6, then PyQt5. PySide6 runs without QtPy; PyQt6 and
PyQt5 use the QtPy compatibility package included in their respective requirements files.
Set `QT_API` before launch to choose explicitly: `pyside6`, `pyqt6`, or `pyqt5`. If PyQt5 is
already installed but QtPy is missing, the startup message asks for only the missing package:
`python -m pip install qtpy` (it does not ask you to reinstall PyQt5).

PyQt5 and PyQt6 are GPL/commercial options; PySide6 offers LGPL/commercial options. Review the
applicable license obligations before distributing a packaged build, especially for closed-source
or commercial use.
**Optional features:**
- Symbol targeting (Tree-Sitter) is not required. Without it the app runs normally and the Symbol
  checkbox is disabled. Install it only when needed: `python -m pip install -r requirements-symbols.txt`.
  Run `python check_treesitter.py` to see which grammars work.
- Pygments syntax highlighting can be installed separately with
  `python -m pip install -r requirements-highlighting.txt`; without it, code viewers remain usable
  as plain text. The two Qt provider files above include Pygments by default.

**No terminal window behind the app**
- **Windows:** double-click **`AI Patch Manager.pyw`**. It runs without any console window.
- `python main.py --detach` (any system) starts the app on its own and gives your terminal back; closing that
  terminal later does not close the app.
- Double-clicking `main.py` on Windows hides its console automatically (it may flash for a moment; use the
  `.pyw` file to avoid even that). A terminal you started the app from is never hidden.
- With no terminal visible, an unexpected error is shown in a dialog and saved to
  `~/.ai-patch-manager/crash.log`. Commands in the Run tab and **Git Status** never open console windows.

---

## Using it

### 1. Project tab: choose what the LLM sees
- **Open Project…** picks a folder; **Open Folder** opens the current project directory in your file manager. **Recent** reopens one you used before (also File ▸ Open Recent).
  Switching to another project clears the pending changes, prompt and selections of the previous one.
- Tick the files you want to send and pick a **mode** for each:

| Mode | What is sent |
|---|---|
| `full` | the whole file |
| `diff` | only what changed since the project was connected (compared with the baseline snapshot) |
| `selection` | only the line ranges you pick |
| `context` | the picked ranges plus a few lines around them (padding: Settings ▸ Context padding) |

- For `selection` / `context`: double-click the file or press **Select Lines for File…**. In the picker:
  **click or drag the line numbers** (or drag over the code) to pick lines; **Ctrl** adds more ranges, and Ctrl on a
  picked line removes it; **Shift+click** extends from the last line you clicked; `Ctrl+A` picks everything.
  Every range stays separate: picked lines stay highlighted, and the **Selected ranges** list on the right lets you
  jump to or remove each one. **Save Ranges** stores them; **Clear Line Selection** removes them. Picked ranges show
  in the tree as `[lines: 10-25, 50-75]`, and the prompt contains exactly those blocks (nothing between them).
- For `full` / `diff`, double-click opens the file in a read-only viewer. At its bottom: **Wrap lines**,
  **Open in Default App** (the program your system associates with the file) and **Open with…**, which opens the
  file in any program you choose. Programs you used before are listed in that button's menu (**Forget saved
  programs** clears them).
- Other buttons: **Select All / Deselect All**, **Rescan Files**, **Rules…** (your own rules, see below),
  **Git Status**.

### 2. Prompt tab: build the prompt
- Write the **Task**. Optionally paste an error or stack trace into **Errors**.
- **Generate XML Prompt** (`Ctrl+G`), then **Copy → Clipboard** (or **Save…**) and paste it into your LLM.
  The line under the preview shows size and estimated tokens.
- **Include in prompt** checkboxes. Anything unchecked is left out entirely, which saves tokens:

| Block | Default | Notes |
|---|---|---|
| `<system>` instructions | **off** | The response format the LLM must follow. Paste it into your LLM **once** (Instructions tab), or tick this box so every prompt carries it. Without either, the LLM does not know the format |
| `<user_rules>` | on | Your own rules, one per line. Set them with **Edit rules…** (or Project tab ▸ **Rules…**, or File ▸ Settings…). Left out automatically when you have none |
| `<errors>` | **off** | Tick it to send the Errors box. An empty box is never sent. The Run tab's **Send output → Errors box** ticks it for you |
| `<files>` content | on | |
| Symbol targeting | **off** | Lets the LLM address functions/classes by name (see below) |
| Binary files (base64) | **off** | Binary files are represented by size + SHA-256 by default; enable this only when the LLM genuinely needs the raw binary payload |

Choices are remembered per project. Every generated prompt contains exactly one **XML-only output contract**: it is part of `<system>` when that option is enabled, or appears as a compact `<output_contract>` block when `<system>` is omitted. It requires one XML response with no prose, questions, Markdown, acknowledgments, or repeated task. If a safe edit cannot be produced, the protocol permits `status=no_changes` with an empty `<changes/>`.

### 3. Response tab: bring the answer back
**Import from Clipboard** or **Import XML File…**, then **Parse + Validate → Changes**. Markdown code fences
(```` ``` ````, ```` ```xml ````, …) that an LLM adds by mistake are removed automatically. **Clear** empties the box. You can edit the reply right
here (`Ctrl+F` search, `Ctrl+H` replace, e.g. to fix a wrong path in every change) and parse it again.

**Copy/paste compatibility:** The Response parser also accepts XML copied from rich-text or Markdown
messages with backslashes before XML tags (such as `\<response>` and `\</status>`),
backslashes at the end of XML lines, and `&#x20;` indentation. This formatting is
normalized automatically when parsing; code inside `<![CDATA[...]]>` retains its
original backslashes and entity text. The original pasted reply remains in the Response
box. A successfully parsed reply still goes through normal file/target validation;
parsing does not guarantee a patch can safely be applied.

### 4. Changes / Preview tab: review and apply
- **Clear Parsed Changes** removes the parsed changes and diff previews without deleting your original XML in the Response tab, modifying project files, or deleting applied history (confirmation required).
- **Double-click a change** to open its existing target file in a code editor. Choose the editor executable on first use; it is remembered. Right-click any change and use **Choose another editor…** to switch editors. This avoids launching source files through executable file associations.
- Check **Edit proposed code** above the diff to open an editable panel under the preview. Edit the proposed **replacement code only** (not its target/anchor), then click **Save & Validate** to refresh the diff and recheck all proposals. Until validation finishes, Apply is disabled for unsaved drafts. Changes to proposals do not touch project files until Apply.
- Each change shows its diff. Untick or **Reject** what you don't want; **Select All** ticks every valid change.
- `Ctrl+F` searches the diff. `Ctrl+H` replaces text inside the **proposed code** of the selected change or of all
  changes (the original lines and the target anchors never change). **Undo** reverts the last replace; edited
  changes are marked ✎.
- **Dry Run** checks that the selected changes can be applied without touching any file.
- **Apply Selected / Apply All** asks for confirmation, makes a backup, then applies everything as one transaction.
- Status per change: *valid*, *invalid* (with the reason), or *conflict* (the file changed after the prompt was
  generated: regenerate the prompt).

### 5. History tab: transaction and single-change undo/redo
Every Apply is a transaction. With **Backup Before Apply** enabled, byte-exact original and post-apply
snapshots are retained for later undo/redo. Select a transaction row to use **Undo Transaction** /
**Redo Transaction** for the whole response. Expand a file row and select one of its
`chg-...` rows to use **Undo Selected Change** / **Redo Selected Change**; the app rebuilds only that file from
the original snapshot and replays the other still-active changes from the same response. Other files in the
transaction are left untouched.

Before any rollback or redo, the app verifies the current file SHA-256 and recorded permissions, checks backup
integrity and destination collisions, and captures a recovery snapshot. If the file changed outside the app,
a backup is missing, replay does not reproduce the expected hash, or saving history fails, the operation is
refused or the pre-operation file is restored. Each partial undo/redo is recorded in the transaction history.

Single-change undo is available for transactions created by versions that store the original change details.
Older history entries show their change IDs but cannot safely undo one change independently; use
**Undo Transaction** instead. The workspace baseline cannot be undone. Double-click a file/change to inspect
its before / after / current versions. History loads the newest 200 transactions first; use **Load Older**
to fetch earlier pages.

### 6. Instructions tab
The fixed instructions and response format the LLM must follow. Because `<system>` is off by default, **Copy
Instructions** and paste them once into your LLM's custom instructions / system prompt (or as the first message
of a chat). Prefer to include them in every prompt instead? Tick `<system>` in the Prompt tab.

### 7. Run tab
Run a command in the project folder (tests, build…). Output appears progressively while the command runs.
The live viewer retains a bounded amount of recent output to stay responsive; **Run Previous Command** repeats the last one,
**Save Current Command** keeps it in the list, **Send output → Errors box** sends the visible output to the Prompt tab.

Long Git status / validation / transaction details open in a resizable, wrapping, copyable text viewer instead of expanding beyond the screen.

**Log panel:** hide/show with View ▸ Show Log, the **Log** button in the status bar, or `Ctrl+Shift+L`; **Clear Log** empties it.

### 8. How to Use tab
This guide, inside the app (also Help ▸ How to Use, or `F1`).

---

## Search, replace and highlighting

- **Search** works in every viewer: files, diffs, prompt preview, response, instructions, run output, log and this
  guide. `Ctrl+F` opens the bar; `Enter` / `F3` next, `Shift+Enter` / `Shift+F3` previous, `Esc` closes. Toggles:
  **Aa** match case · **W** whole word · **.\*** regular expression.
- **Replace** (`Ctrl+H`) exists only where the text is meant to be edited: the **Response** box (edits the reply
  text, one `Ctrl+Z` undoes Replace All) and the **Changes** tab (edits the proposed code, see above). Everything else
  is read-only on purpose: files, the prompt preview (Copy sends the generated prompt, not the preview), the
  instructions, history and logs must stay exact.
- **Wrap Text** (View menu, `Alt+Z`; **on by default** for new installations; also a **Wrap lines** checkbox inside the file viewer and the line picker,
  which are modal and hide the menu): long lines wrap in the code, diff, prompt and instruction viewers instead of
  scrolling sideways. Remembered between launches (an existing choice is preserved). The Response, Task, Errors, Run output and Log boxes always wrap, including long words/paths. Long file paths and status labels stay inside the screen.
- **Code is syntax-highlighted** in file viewers, the line picker, diffs (each file in its own language), the prompt
  preview, the response and the instructions. The language comes from the file extension (needs the optional
  `pygments` package from `requirements-highlighting.txt`; without it the viewers simply show plain text).
  Light and dark themes are both supported.
- The file viewer and the line picker show **line numbers** that always stay in step with the code. In the file viewer,
  click a number to select that line, drag (or Shift+click) to select several; the picker has its own controls (see the
  Project tab).

---

## What the LLM sends back (response format)

A single XML document. Code always goes inside `<![CDATA[ … ]]>`.

```xml
<response version="1.0">
  <status>success</status>
  <summary>Rename helper and add an import</summary>
  <changes>
    <change id="chg-001" path="app.py" operation="replace">
      <target><match><![CDATA[def old_name(x):]]></match></target>
      <replacement><![CDATA[def new_name(x):]]></replacement>
      <reason>Clearer name</reason>
    </change>
    <change id="chg-002" path="app.py" operation="append">
      <target><match><![CDATA[import os]]></match></target>
      <replacement><![CDATA[import sys]]></replacement>
    </change>
  </changes>
</response>
```

**Operations**

| Operation | Meaning |
|---|---|
| `replace` | replace the targeted code |
| `append` | without `<target>`: add at the **end of the file**. With `<target>`: add directly **below** that line/block |
| `prepend` | without `<target>`: add at the **start of the file**. With `<target>`: add directly **above** that line/block |
| `create` | new file (`<replacement>` is its content) |
| `delete` | delete the file |
| `rename` | move the file (`<new_path>`); the destination must not exist yet (nothing is ever overwritten) |

**Targets: how a change says *where***

| Target | Use it for |
|---|---|
| `<symbol>` (`<type>`, `<name>`, optional `<class>`, `<parameters>`) | a whole function / method / class, found by name (off by default) |
| `<match>` | an exact piece of code, copied from the file. The safest choice |
| `<line_start>` / `<line_end>` (optional `<hash>`) | when line numbers are known |
| `<regex>` | simple structural edits (renames, imports, signatures). On `replace` it changes **every** match; as an `append`/`prepend` anchor it must match once |

**Rules the app enforces**
- A `<match>`, regex anchor or symbol must identify **exactly one place**. Zero matches or two or more is rejected
  (the message lists the lines so the LLM can add a neighbouring line).
- New code replaces/inserts **whole lines**, so the replacement must keep the original indentation.
- Several changes may touch the same file, but two changes may not touch the same lines
  (a `prepend` and an `append` around one anchor is fine).
- Paths must stay inside the project (unless *Allow External Paths* is ticked in Settings).
- A rename onto a file that already exists is rejected, as are two renames to the same destination.
- Leading and trailing blank lines inside `<replacement>` are trimmed.

---

## Symbol targeting (optional)

Tick **Symbol targeting** (Prompt tab or Instructions tab) and the LLM may target code by name instead of text:
`<symbol><type>function</type><name>calculate_total</name></symbol>`. The app finds the exact start and end of the
function using Tree-Sitter, including nested blocks.

- Languages: **Python, JavaScript, TypeScript / TSX, Java, C / C++** (types: function, method, class, struct,
  interface, enum). Other languages: use `<match>`.
- The replacement overwrites the symbol's full lines (decorators are not part of a Python symbol).
- Tree-Sitter is optional and is not part of the normal installation. If it is absent, the Symbol checkbox is
  disabled with the install command; all other target modes continue to work. Install the optional packages with
  `python -m pip install -r requirements-symbols.txt`. If only some grammars are installed, Symbol targeting is
  enabled only for grammars that successfully load; `python check_treesitter.py` reports the available languages.

---

## Settings (File ▸ Settings…)

| Setting | Default | Meaning |
|---|---|---|
| Exact Match | on | locate code by its exact text |
| Hash Validation | on | verify a `<hash>` given with a line range |
| Context Match | on | accept a line range whose text is ≥95 % similar to the given `<match>` |
| Fuzzy Match / Threshold | off / 0.90 | approximate matching; always flagged and needs explicit approval |
| Require Preview / Confirmation | on | show the diff, ask before applying |
| Backup Before Apply | on | keep a backup so a transaction can be undone |
| Conflict Detection | on | flag changes for files that changed after the prompt was generated |
| Allow External Paths | off | **dangerous**: lets changes touch files outside the project |
| Context padding | 3 | lines added around picked ranges in `context` mode |
| User-defined Rules | none | your rules, one per line (sent as `<user_rules>`) |

---

## Where things are stored

| What | Where |
|---|---|
| Per-project settings, rules, selected files and line ranges, history, backups, saved commands | `<your project>/.ai-patch-manager/` (`config.json`, `history.json`, `backups/`, `saved_commands.json`) |
| Recent projects | `~/.ai-patch-manager/recent_projects.json` (set `AI_PATCH_MANAGER_HOME` to move it) |
| Recoverable UI errors and tracebacks | `~/.ai-patch-manager/ui_errors.log` (also respects `AI_PATCH_MANAGER_HOME`) |
| Programs for **Open with…** | `~/.ai-patch-manager/open_with.json` |
| "Wrap Text" choice, log visibility | your user settings (kept by Qt, per user) |
| Last opened project, default settings | `config/app.xml` in the app folder |

New snapshots are simple independent byte-exact copies, with collision-resistant names. The app no longer creates or uses `.blobs` for new snapshots. **Keep old `.blobs/` folders and existing backups untouched** if upgrading from Phase 2/3. The latest entries in an existing `history.jsonl` are imported automatically into `history.json`; the journal is archived as `history.jsonl.migrated-...` and is not deleted. Back up `.ai-patch-manager/` before the first launch of this update. Add `.ai-patch-manager/` to your project's `.gitignore`.

See [PHASES_1_TO_3_REEVALUATION.md](docs/changelog/releases/PHASES_1_TO_3_REEVALUATION.md) for a change-by-change review. The original PHASE1/2/3 optimization notes remain historical and describe the previous implementation.

---

## Troubleshooting

| You see | Do this |
|---|---|
| `anchor is ambiguous: it matches 2+ places` | the text occurs several times; use a longer `<match>` (add the next line) |
| `target not found` | the LLM's text doesn't exactly match the file; check the file wasn't edited, regenerate the prompt, send it again |
| `CONFLICT: the file changed after the prompt was generated` | regenerate the prompt so the LLM sees the current file |
| `overlaps with chg-…` | two changes edit the same lines; merge them into one |
| `inserted code starts at column 0 but the anchor line is indented` (warning) | check indentation, especially in Python |
| Symbol targeting is disabled | Install `python -m pip install -r requirements-symbols.txt`, restart the app, and run `python check_treesitter.py` |
| Qt binding cannot be loaded | Existing PySide6 needs no QtPy. PyQt6/PyQt5 need QtPy; if the binding is already installed, install only `python -m pip install qtpy`. Use the matching requirements file for a fresh environment. |
| a tab, save, import or project open operation reports a recoverable error | See the Log dock and the per-user `ui_errors.log` traceback for details |

---

## Keyboard shortcuts

`Ctrl+G` generate prompt · `Alt+Z` wrap text · `Ctrl+F` find · `F3` / `Shift+F3` next / previous · `Ctrl+H` replace (where allowed) · `Ctrl+Shift+L` show/hide log · `F1` open the How to Use tab

---

## For developers

```
main.py          entry point
core/            parser, validator, patch/diff engines, transactions + history, Tree-Sitter, prompt builder
models/          Change, Project/Settings, Transaction
ui/              one module per tab, main window, dialogs
assets/          app icon (edit the SVGs, run `python assets/make_icons.py`)
```

Targets are resolved in this order: symbol, exact match, line range, regex. Every change is located and validated
against the file as it is now, then applied deterministically as one transaction with a backup.

Phase 2 adds SHA-256 backup deduplication, append-only history, and streamed command output.
Prompt files are now read freshly from disk on every generation; the former in-memory prompt-file
cache was removed to simplify behavior and avoid stale-file problems. Token counts are estimates,
not exact counts from a specific model tokenizer.


### Phase 3 enhancements

Search input is debounced (200 ms) to reduce repeated work on long files; Find Next/Previous remains immediate.
Saved Run commands are stored atomically per workspace. Inactive project tabs refresh when selected, and the How-to-Use Markdown guide loads only on first visit.
When switching projects, previous project input is cleared from the UI. A command already started in the Run tab continues in its original working directory; its output will not be shown under the new project.
Error reporting is improved with a bounded per-user `ui_errors.log`. See [PHASE3_OPTIMIZATIONS.md](docs/changelog/releases/PHASE3_OPTIMIZATIONS.md) for test coverage and limitations.

### Resizable layout preferences
- Resize columns in **Project** and **History**, resize the **Changes** and **History** split panels, and resize the main window to suit your screen. Sizes are saved in the user's Qt application settings and restored on next launch.
- Layout preferences are shared across projects; project-specific options remain in `.ai-patch-manager/config.json`.

See [FEATURES_2026-11-01.md](docs/changelog/releases/FEATURES_2026-11-01.md) for the full changes and testing notes.


### Optional project tree in prompts

The Prompt tab has an unchecked-by-default **Project tree (paths only)** option. When enabled, the prompt includes a hierarchical `<project_tree>` block listing scanned directories and file names, without reading or sending file contents. It is independent of the `<files> content` option and respects the project's current include/exclude scan rules. Refresh the Project tab after filesystem changes before generating a prompt.

The **Clear Parsed Changes** button is available at the bottom of the Changes tab. It clears parsed proposals and diff previews while retaining the original response XML and applied transaction history.

### Optional essential note in LLM responses

A response may include one `<note type="warning">Important action or risk</note>`
(or `type="info"`) **only when the user must know something essential**.
Warning notes appear in the Changes tab and in a dialog after an explicit Parse;
info notes appear only in the Changes tab. Both are unverified AI-generated
text and cannot override validation. See [NOTE_FEATURE.md](docs/reference/NOTE_FEATURE.md).


### Copy/paste recovery: standalone numeric spaces in CDATA

The parser recovers an isolated `&#x20;` / `&#32;` in indentation and even an
entity-only CDATA line. See [SINGLE_CDATA_SPACE_ENTITY_FIX.md](docs/changelog/fixes/SINGLE_CDATA_SPACE_ENTITY_FIX.md) for behavior
and the ambiguity warning about intentionally literal HTML entities.


### Hybrid rollback storage (2026-11-16)

New transactions keep one immutable byte-exact `.orig` snapshot. Redo uses a
verified line-based byte delta when it is at most 70% of the post-Apply file
size (files up to 2 MiB / 20,000 lines); otherwise it falls back to a full
`.new` snapshot. Rename-only files can reuse `.orig` when bytes and permissions
are unchanged, and deletions use a marker. Single-change Undo/Redo no longer
persists another full `.current` snapshot; History previews reconstruct partial
states from `.orig` and the recorded active changes. Legacy `.new` and `.current`
snapshots remain readable. See [HYBRID_ROLLBACK_OPTIMIZATION.md](docs/reference/HYBRID_ROLLBACK_OPTIMIZATION.md).
