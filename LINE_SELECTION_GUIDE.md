# Line Selection Feature — Complete Guide

## What Is It?

The **line selection feature** lets you choose specific line ranges for files when using **selection** or **context** modes. This is useful when a file is too large and you only want the LLM to see certain functions or sections.

---

## How to Use

### Step 1: Select a File

In the **Project tab**, check a file you want to include.

### Step 2: Choose a Mode

For each file, pick a mode from the dropdown:

| Mode | Behavior |
|------|----------|
| **full** | Include entire file in prompt |
| **diff** | Include only the changed parts |
| **selection** | Include ONLY the lines you choose |
| **context** | Include selected lines + padding around them |

### Step 3: Select Lines (for selection/context modes)

When you've set a file to **selection** or **context** mode:

**Option A: Click "Select Lines for File..." button**
- A dialog opens showing the file with line numbers
- Click and drag to select the lines you want
- Or use Shift+Click for multiple ranges
- Click "Save Ranges" to save your selection

**Option B: Double-click the file in the tree**
- The line selection dialog opens directly

### Step 4: Visual Feedback

After selecting lines, you'll see them highlighted in blue in the file tree:

```
myfile.py  [lines: 10-25, 50-75]
```

This shows that lines 10-25 and 50-75 are selected.

---

## Modes Explained

### Full Mode
```
entire file included in prompt
|====================================|
```

### Diff Mode
```
only changed lines included
|......|XXX|......|XX|......|
```

### Selection Mode
```
only your selected lines included (no padding)
|......|XXX|......|XXX|......|
         ↑           ↑
      selections
```

### Context Mode
```
your selections + padding lines around them
|......|XXXXX|....|XXXXX|......|
```

Context mode is useful because the LLM sees the surrounding code for context, not just the isolated lines.

---

## Example Workflow

### Scenario: Large controller.m file

You have a 500-line MATLAB file but only want the LLM to fix the `calculateControl` function (lines 120-180).

**Steps:**

1. Check `controller.m` in the file list
2. Change mode to **context** (not selection, because you want context lines)
3. Click **"Select Lines for File..."**
4. In the dialog, select lines 120-180
5. Click **"Save Ranges"**

**Result:**
- Prompt includes lines ~100-200 (your 120-180 + surrounding context)
- LLM sees the function signature, local variables, and called functions
- Much better than sending the entire 500-line file

---

## Clearing Selections

Click **"Clear Line Selection"** to remove any line range you've set for a file.

The file will then include all lines (or diff lines) instead of just the selected range.

---

## Tips & Tricks

### Multiple Ranges Per File

You can select **multiple separate ranges** in the dialog:

1. Drag to select lines 10-20
2. Hold Ctrl, drag to select lines 50-60
3. Hold Ctrl, drag to select lines 100-110
4. Click "Save Ranges"

Result: `[lines: 10-20, 50-60, 100-110]`

### Quick Selection

In the dialog:
- Click "Select All Lines" → selects entire file
- Click "Clear Selection" → deselects all
- Shift+Click to extend selection

### Estimating Token Cost

The status bar shows:
```
Prompt: 1500 lines · ~375 tokens
```

If you select only 50 lines instead of 1500, tokens drop to ~12. This matters for expensive APIs.

---

## Why This Exists

### Problem

Files can be huge (500+ lines), but you only want the LLM to see specific functions or sections.

Sending the entire file:
- Wastes API tokens
- Distracts the LLM with unrelated code
- Costs more money

### Solution

Use **selection** or **context** mode + pick the lines you care about.

The LLM sees only what matters → better patches, lower cost.

---

## Common Questions

### Q: What's the difference between selection and context?

**Selection:** Include ONLY the selected lines (no surrounding code)
- Use when lines are self-contained
- Minimal context

**Context:** Include selected lines + surrounding padding  
- Use when the LLM needs to see the surrounding code
- Better for understanding function signatures, imports, variable definitions

### Q: Can I select non-contiguous lines?

**Yes!** Drag to select range 1, Ctrl+drag to add range 2, etc.

### Q: Does double-click work on folders?

No. Double-clicking a folder just expands/collapses it. You must select a file first.

### Q: Can I change selections after choosing them?

Yes. Select the file again, click "Select Lines for File...", and choose new ranges. Old selections are replaced.

### Q: What if I want the entire file in selection mode?

Click "Select All Lines" in the dialog. All lines will be marked as selected.

---

## Keyboard Shortcuts (In Line Selection Dialog)

| Action | Shortcut |
|--------|----------|
| Select a range | Click and drag |
| Extend selection | Shift+Click |
| Add to selection | Ctrl+Click and drag |
| Deselect all | Click "Clear Selection" button |
| Select all | Click "Select All Lines" button |

---

## Troubleshooting

### "Select a file first, then click..."
You need to click on a file in the tree before clicking "Select Lines for File...".

### Lines not showing up in prompt
Make sure:
1. File is checked (checkbox is ✓)
2. Mode is set to **selection** or **context** (not full or diff)
3. You've actually saved the line selection (click "Save Ranges")

### Changes not saved
The line selections are saved to `.ai-patch-manager/config.json` in your project directory. If you restart the app, your selections should persist.

---

## Summary

| Step | Action |
|------|--------|
| 1 | Check file in project list |
| 2 | Set mode to selection or context |
| 3 | Click "Select Lines for File..." or double-click file |
| 4 | Drag to select lines in the dialog |
| 5 | Click "Save Ranges" |
| 6 | See `[lines: ...]` label in file tree (visual confirmation) |
| 7 | Generate prompt — selected lines are included |

The line selection feature is now **fully visible, documented, and easy to use**.
