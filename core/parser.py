"""Secure XML parser for LLM responses (sections 11, 38, 39, 40, 41, 80).

The XML coming back from any LLM is UNTRUSTED input:
- no network access, no DTD loading, no entity resolution (XXE defence)
- strict element whitelist; unknown elements are reported, never executed
- everything is converted into internal `Change` objects; downstream code
  must never look at XML again (section 65).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from lxml import etree

from models.change import Change, Target, VALID_OPERATIONS

SUPPORTED_VERSIONS = {"1.0"}


class ProtocolError(Exception):
    """Raised when the response cannot be parsed at all."""


@dataclass(frozen=True)
class ResponseNote:
    """Optional, untrusted message from the LLM; never a validation result."""

    type: str  # 'warning' or 'info'
    text: str



def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,     # block internal & external entities
        no_network=True,            # never fetch remote resources
        dtd_validation=False,
        load_dtd=False,             # ignore DTDs entirely
        huge_tree=False,
    )


_XML_DECL = re.compile(r"<\?xml[^>]*\?>", re.IGNORECASE)
# --- markdown code fences (```, ```xml, ```python, ```<anything>) ----------------
# A fence LINE: backticks + optional info string, nothing else on the line.
_FENCE_LINE = re.compile(r"^[ \t]*`{3,}[^\n`<]*(?:\n|\Z)", re.MULTILINE)
# Left-over fence glued to other text: ```<response ...>, </response>```, ```xml <response>
_FENCE_INLINE = re.compile(r"`{3,}[ \t]*(?:[A-Za-z][\w+#.\-]*[ \t]*(?=<))?")
# A fence PAIR wrapping the whole content of one CDATA section
_CD_OPEN = re.compile(r"\A(\s*)`{3,}[^\n`]*\n")
_CD_CLOSE = re.compile(r"\n[ \t]*`{3,}[ \t]*(\s*)\Z")


def _clean_outside(segment: str) -> Tuple[str, int]:
    """Remove every fence from XML structure / chatter (outside CDATA)."""
    segment, n1 = _FENCE_LINE.subn("", segment)
    segment, n2 = _FENCE_INLINE.subn("", segment)
    return segment, n1 + n2


def _unwrap_cdata(content: str) -> Tuple[str, int]:
    """LLMs sometimes wrap the code INSIDE <![CDATA[ ]]> in ```lang ... ```.
    Only a fence pair that wraps the whole content is removed, so a Markdown
    file that legitimately contains code blocks keeps them."""
    m1, m2 = _CD_OPEN.match(content), _CD_CLOSE.search(content)
    if m1 and m2 and m1.end() <= m2.start() + 1:
        return m1.group(1) + content[m1.end():m2.start()] + m2.group(1), 2
    return content, 0


def strip_code_fences(text: str) -> Tuple[str, int]:
    """Remove accidental markdown code fences from an LLM response.

    Handles ```, ```xml, ```python, ```<anything> - at the start/end, between
    elements, glued to a tag, or wrapped around the code inside a CDATA
    section. Returns (clean_text, number_of_fences_removed).
    """
    out: List[str] = []
    removed, pos = 0, 0
    while True:
        i = text.find("<![CDATA[", pos)
        if i == -1:
            seg, n = _clean_outside(text[pos:])
            out.append(seg); removed += n
            break
        seg, n = _clean_outside(text[pos:i])
        out.append(seg); removed += n
        j = text.find("]]>", i + 9)
        if j == -1:                       # truncated reply: leave the tail untouched
            out.append(text[i:])
            break
        inner, n = _unwrap_cdata(text[i + 9:j])
        out.append("<![CDATA[" + inner + "]]>")
        removed += n
        pos = j + 3
    return "".join(out), removed


_UNQUOTED_ATTR = re.compile(r'(<[\w:-]+(?:\s+[\w:-]+)=(["\'])(?:[^"\']*)\2(?:\s+[\w:-]+=(?!["\'])[^>\s/]+)+[^>]*)')


def _fix_unquoted_attrs(fragment: str) -> str:
    """Quote unquoted attribute values inside tags (common LLM mistake),
    e.g. ``<response version=1.0>`` -> ``<response version="1.0">``."""
    def _quote_tag(m):
        tag = m.group(0)

        def _attr(am):
            return f'{am.group(1)}="{am.group(2)}"'

        return re.sub(r'([\w:-]+)=([^\s>"\']+)', _attr, tag)

    return re.sub(r"<[^<>]+>", _quote_tag, fragment)


def extract_xml(text: str) -> str:
    """LLMs often wrap the XML in markdown fences or chatter - pull it out.

    Robust against: leading/trailing prose, ```xml fences (closed OR
    truncated), BOM, extra whitespace, and a missing final </response>.
    """
    text = text.lstrip("\ufeff").strip()
    # remove every accidental markdown fence (```, ```xml, ```python, ```<anything>)
    text, _ = strip_code_fences(text)
    text = text.strip()

    start = text.find("<response")
    if start == -1:
        raise ProtocolError("No <response> XML found in the pasted text.")
    text = text[start:]
    end = text.rfind("</response>")
    if end != -1:
        text = text[:end + len("</response>")]
    else:
        # truncated reply - close any open CDATA and pending elements so
        # lxml can still parse what we received
        text = text.rstrip()
        if text.count("<![CDATA[") > text.count("]]>"):
            text += "]]>"
        unclosed = []
        for tag in ("replacement", "content", "match", "target", "change", "changes"):
            opened = len(re.findall(rf"<{tag}[\s>]", text))
            closed = text.count(f"</{tag}>")
            unclosed += [tag] * max(0, opened - closed)
        text += "".join(f"</{t}>" for t in unclosed) + "</response>"
    text = _XML_DECL.sub("", text).strip()
    return text


# Markdown/rich-text copy can escape XML MARKUP and, in some clients,
# decorate each pasted SOURCE line with a trailing backslash and/or a literal
# &#x20; indentation token.  Parsing XML alone is not enough: CDATA still
# contains those presentation artifacts, so exact-match validation fails.
# Repair only when there is clear evidence of copy formatting.  Do not run
# generic HTML unescaping over arbitrary code (which can destroy valid code).
_ESCAPED_XML_TAG = re.compile(
    r"(?<!\\)\\(?=<(?:/?[A-Za-z_][\w:.-]*(?=[\s/>])|!--|\?xml\b))"
)
_ESCAPED_LINE_END = re.compile(r"(?<=>)\\(?=[ \t]*(?:\r?\n|\Z))")
_CDATA_REGION = re.compile(r"\\?<!\[CDATA\[.*?\]\]>", re.DOTALL)
_LEADING_SPACE_ENTITY = re.compile(
    r"(?im)^[ \t]*(?:(?:&#x20;|&#32;)[ \t]*)+"
)
_SPACE_ENTITY = re.compile(r"&#(?:x20|32);", re.IGNORECASE)
_CDATA_LINE_SLASH = re.compile(r"\\(?=\r?\n|\Z)")
# Even one &#x20; can be a copy artifact when it occupies the indentation
# position before a code line.  Require at least two real spaces (or a tab)
# after the entity, so a literal explanation such as "&#x20; is a space"
# does not get rewritten merely because it is in CDATA. No code/regex
# string containing an entity in the middle of the line can match here.
_SINGLE_ENTITY_INDENT = re.compile(
    # One encoded indentation token followed by real indentation, or a
    # standalone line made only of numeric space entities. The latter also
    # supports CDATA whose entire payload is just &#x20;.
    r"(?im)^[ \t]*(?:&#(?:x20|32);)(?:(?:[ \t]{2,}|\t)(?=\S)|"
    r"[ \t]*(?:&#(?:x20|32);[ \t]*)*(?=\r?$))"
)


def _cdata_payloads(text: str) -> list[str]:
    """Yield only the literal code within CDATA, not its XML delimiters."""
    return [m.group(0).split('<![CDATA[', 1)[1][:-3]
            for m in _CDATA_REGION.finditer(text)]


def _has_transport_markup(text: str) -> bool:
    """An escaped XML tag or end-of-tag backslash is a copy-format signal."""
    outside = _CDATA_REGION.sub("", text)
    return bool(_ESCAPED_XML_TAG.search(outside)
                or _ESCAPED_LINE_END.search(outside))


def _cdata_copy_signature(text: str) -> bool:
    """Fallback when outer XML is normal but pasted CDATA remains decorated.

    Require both repeated encoded INDENTATION and repeated end-of-line
    slashes. A normal code string containing an HTML entity or regex must
    never trigger this recovery by itself.
    """
    for inner in _cdata_payloads(text):
        # A single encoded indentation token in otherwise normal, valid XML
        # must also be restored.  Requiring an indentation layout avoids
        # interpreting ordinary HTML entities in strings and documentation.
        if _SINGLE_ENTITY_INDENT.search(inner):
            return True
        encoded = len(_LEADING_SPACE_ENTITY.findall(inner))
        line_slashes = sum(1 for line in inner.splitlines()
                           if line.rstrip(' \t').endswith('\\'))
        if encoded >= 2 and line_slashes >= 2:
            return True
    return False


def _clean_pasted_cdata(content: str, copy_evidence: bool) -> str:
    """Restore only leading indentation and transport-created line endings.

    The recovery is deliberately conservative. It does not interpret HTML
    character references embedded in strings or strip ordinary backslashes
    within paths and regular expressions. Trailing slashes are removed only
    when they appear on most source lines (as with Markdown hard breaks).
    """
    if not copy_evidence:
        return content

    source_lines = [ln for ln in content.splitlines() if ln.strip()]
    slashed_lines = sum(1 for ln in source_lines if ln.rstrip(' \t').endswith('\\'))
    # One literal &#x20; at the start of an HTML/documentation line may be
    # intentional. Require a repeated indentation pattern or the repeated
    # trailing-backslash pattern typical of rich-text copies.
    entity_indents = len(_LEADING_SPACE_ENTITY.findall(content))
    if entity_indents >= 2 or slashed_lines >= 2 or _SINGLE_ENTITY_INDENT.search(content):
        content = _LEADING_SPACE_ENTITY.sub(
            lambda m: _SPACE_ENTITY.sub(' ', m.group(0)), content)
    if len(source_lines) >= 2 and slashed_lines >= 2 and slashed_lines / len(source_lines) >= 0.65:
        # Only remove the final transport backslash, even for a line that
        # originally ended in a legitimate backslash or double backslash.
        content = _CDATA_LINE_SLASH.sub('', content)
    return content


def normalize_escaped_xml_copy(text: str) -> str:
    """Repair XML copied with Markdown escapes, including pasted CDATA.

    Only run in a response with strong copy-format evidence. For existing
    valid XML with ordinary code/regex backslashes, this is an identity
    operation. No global XML or HTML entity-decoding is performed.
    """
    copy_evidence = _has_transport_markup(text) or _cdata_copy_signature(text)
    if not copy_evidence:
        return text

    def outside(fragment: str) -> str:
        fragment = _ESCAPED_XML_TAG.sub("", fragment)
        return _ESCAPED_LINE_END.sub("", fragment)

    result: List[str] = []
    pos = 0
    for region in _CDATA_REGION.finditer(text):
        result.append(outside(text[pos:region.start()]))
        token = region.group(0)
        # The backslash before the CDATA opener is markup, not source code.
        if token.startswith(r"\<![CDATA["):
            token = token[1:]
        inner = token[len('<![CDATA['):-3]
        result.append('<![CDATA[' + _clean_pasted_cdata(inner, copy_evidence) + ']]>')
        pos = region.end()
    result.append(outside(text[pos:]))
    return "".join(result)


def _looks_like_escaped_copy(raw: str, xml: str) -> bool:
    """Check both the raw pasted wrapper and the extracted response XML."""
    return (_has_transport_markup(raw) or _has_transport_markup(xml)
            or _cdata_copy_signature(xml))


def _text_of(el, strip: bool = True) -> Optional[str]:
    if el is None or el.text is None:
        return None
    return el.text.strip() if strip else el.text


def parse_response_with_note(raw: str) -> Tuple[str, str, str, List[Change], Optional[ResponseNote]]:
    """Parse XML and return (status, summary, legacy_notes, changes, optional_note).

    The optional response-level <note> is untrusted user-facing information,
    not a validator warning or an instruction to execute an action.
    """
    xml = extract_xml(raw)
    if _looks_like_escaped_copy(raw, xml):
        xml = normalize_escaped_xml_copy(xml)
    try:
        root = etree.fromstring(xml.encode("utf-8"), parser=_safe_parser())
    except etree.XMLSyntaxError as exc:
        # Try the existing unquoted-attribute recovery first. Only if that
        # fails, repair backslash-escaped markup introduced when copying from
        # Markdown/chat. This preserves valid XML (including literal code).
        candidates = [_fix_unquoted_attrs(xml)]
        normalized = normalize_escaped_xml_copy(xml)
        if normalized != xml:
            candidates.extend((normalized, _fix_unquoted_attrs(normalized)))
        root = None
        for candidate in candidates:
            try:
                root = etree.fromstring(candidate.encode("utf-8"),
                                        parser=_safe_parser())
                break
            except etree.XMLSyntaxError:
                continue
        if root is None:
            raise ProtocolError(f"Malformed XML: {exc}") from exc

    if root.tag != "response":
        raise ProtocolError(f"Root element must be <response>, got <{root.tag}>")

    version = root.get("version", "1.0")
    if version not in SUPPORTED_VERSIONS:
        raise ProtocolError(f"Unsupported protocol version '{version}' "
                            f"(supported: {', '.join(sorted(SUPPORTED_VERSIONS))})")

    status = _text_of(root.find("status")) or "unknown"
    summary = _text_of(root.find("summary")) or ""
    notes = _text_of(root.find("notes")) or ""
    # New response-level note is optional, with at most one per response.
    # Treat unknown types as ordinary information rather than elevating
    # AI-generated content to a warning.
    if len(root.findall("note")) > 1:
        raise ProtocolError("Only one response-level <note> is allowed.")
    note_el = root.find("note")
    note_text = _text_of(note_el) or ""
    note_type = (note_el.get("type", "info") if note_el is not None else "info").lower().strip()
    if note_type not in {"info", "warning"}:
        note_type = "info"
    # Keep the UI bounded even for unexpectedly huge model-generated notes.
    note = (ResponseNote(note_type, note_text[:4000] + ("…" if len(note_text) > 4000 else ""))
            if note_text else None)

    changes_el = root.find("changes")
    change_els = changes_el.findall("change") if changes_el is not None else []
    if not change_els and status.lower() not in {"no_changes", "error"}:
        raise ProtocolError(
            "Response contains no <change> elements. For a safe no-op reply, "
            "use <status>no_changes</status> (or <status>error</status>) in the XML.")

    changes: List[Change] = []
    seen_ids = set()
    for idx, ch in enumerate(change_els):
        cid = ch.get("id") or f"chg-{idx + 1:03d}"
        if cid in seen_ids:
            raise ProtocolError(f"Duplicate change id '{cid}'.")
        seen_ids.add(cid)

        path = ch.get("path")
        operation = (ch.get("operation") or "").strip()
        if not path:
            raise ProtocolError(f"Change '{cid}' is missing the 'path' attribute.")
        if operation not in VALID_OPERATIONS:
            raise ProtocolError(
                f"Change '{cid}' has invalid operation '{operation}'. "
                f"Valid: {', '.join(sorted(VALID_OPERATIONS))}")

        target_el = ch.find("target")
        target = Target()
        if target_el is not None:
            match_el = target_el.find("match")
            target.match = _text_of(match_el, strip=False)
            if target.match is not None:
                target.match = target.match.strip("\n")
            target.hash = _text_of(target_el.find("hash")) or None
            ls = _text_of(target_el.find("line_start"))
            le = _text_of(target_el.find("line_end"))
            try:
                target.line_start = int(ls) if ls else None
            except ValueError:
                target.line_start = None
            try:
                target.line_end = int(le) if le else None
            except ValueError:
                target.line_end = None
            # regex mode: Python re pattern; replace-all with backreferences
            regex_el = target_el.find("regex")
            target.regex = _text_of(regex_el, strip=True) or None
            
            # symbol mode: Tree-Sitter structural targeting
            symbol_el = target_el.find("symbol")
            if symbol_el is not None:
                from models.change import SymbolTarget
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

        content_el = ch.find("content") if ch.find("replacement") is None else None
        repl_el = ch.find("replacement")
        content_el = repl_el if repl_el is not None else content_el
        content = _text_of(content_el, strip=False) if content_el is not None else None
        if content is not None:
            content = content.strip("\n")

        new_path = _text_of(ch.find("new_path"))

        conf = _text_of(ch.find("confidence"))
        confidence: Optional[float] = None
        if conf:
            try:
                confidence = float(conf)
            except ValueError:
                confidence = None

        changes.append(Change(
            id=cid,
            path=path.strip(),
            operation=operation,
            target=target,
            content=content,
            new_path=new_path.strip() if new_path else None,
            summary=_text_of(ch.find("summary")) or "",
            reason=_text_of(ch.find("reason")) or "",
            confidence=confidence,
        ))

    return status, summary, notes, changes, note


def parse_response(raw: str) -> Tuple[str, str, str, List[Change]]:
    """Legacy four-element API, kept for existing integrations and tests."""
    status, summary, notes, changes, _note = parse_response_with_note(raw)
    return status, summary, notes, changes
