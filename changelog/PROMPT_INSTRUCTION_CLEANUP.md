# Prompt instruction cleanup

## Why the previous output looked wrong

The previous implementation appended several overlapping XML-only rules to the existing protocol and serialized the entire response schema as escaped text inside an `<instruction>` element. That made the Instructions tab look unnecessarily large and showed schema tags as `&lt;...&gt;` entities.

## Changes

- Consolidated XML-only behavior into one output-contract instruction.
- Removed duplicated output-only and no-change directives.
- Removed the implementation-only "Avoid nested CDATA issues" instruction.
- Rendered all human-readable rules together in one `<instructions><![CDATA[...]]></instructions>` block.
- Rendered the response schema as actual nested XML under `<response_schema>` instead of escaped text.
- Included the output contract only once: in `<system>` when system instructions are enabled, or in a compact `<output_contract>` block when they are disabled.
- Updated the Instructions tab hint to explain when the full protocol is included in generated prompts.

## Verification

- XML parsing of the reusable instruction document: passed.
- XML parsing of generated prompts with system instructions on and off: passed.
- Project-tree and no-change protocol regression tests: passed.
- CDATA terminator handling: passed.
- GUI tests remain dependent on PySide6 and must be run on Windows.
