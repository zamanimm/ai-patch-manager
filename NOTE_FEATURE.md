# Essential AI note (optional XML)

AI Patch Manager can now read one optional **response-level** `<note>` element.

```xml
<response version="1.0">
    <status>success</status>
    <summary>Added the requested integration.</summary>
    <note type="warning">A required API key must be configured before running.</note>
    <changes>
        <!-- Valid changes appear here, as usual. -->
    </changes>
</response>
```

- The model is instructed to **omit `<note>` unless there is something important the user MUST know**, such as a risk, mandatory configuration, new dependency, or essential manual action. Do not repeat summaries or routine explanations.
- `type="warning"`: the text is prominently displayed in **Changes / Preview** and shown in a warning dialog **once per explicit Parse + Validate** action. Revisiting tabs does not create extra dialogs.
- `type="info"`: displays a neutral note in **Changes / Preview**, with no modal dialog.
- When no note exists, UI behavior is unchanged. An unknown type is treated as ordinary info; multiple notes are rejected. A missing `type` is treated as info.
- Note content is **model-supplied, unverified text**, shown as plain text. It never triggers commands, blocks Apply, or substitutes for real validation.
- Previous `<notes>` (plural) remains compatible, and the old four-value `parse_response()` API is retained.
- Clearing parsed changes also clears the displayed note, while keeping the raw response XML.

## Example with no note

```xml
<response version="1.0">
    <status>no_changes</status>
    <summary>No safe change was possible from the provided context.</summary>
    <changes/>
</response>
```

## Testing

Run `python -m unittest discover -s tests` after installing requirements. The GUI tests additionally require PySide6 and should be run on Windows for final visual verification.
