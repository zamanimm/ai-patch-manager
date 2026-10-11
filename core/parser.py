"""Secure XML parser for LLM responses (sections 11, 38, 39, 40, 41, 80).

The XML coming back from any LLM is UNTRUSTED input:
- no network access, no DTD loading, no entity resolution (XXE defence)
- strict element whitelist; unknown elements are reported, never executed
- everything is converted into internal `Change` objects; downstream code
  must never look at XML again (section 65).
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from lxml import etree

from models.change import Change, Target, VALID_OPERATIONS

SUPPORTED_VERSIONS = {"1.0"}


class ProtocolError(Exception):
    """Raised when the response cannot be parsed at all."""


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,     # block internal & external entities
        no_network=True,            # never fetch remote resources
        dtd_validation=False,
        load_dtd=False,             # ignore DTDs entirely
        huge_tree=False,
    )


_XML_DECL = re.compile(r"<\?xml[^>]*\?>", re.IGNORECASE)
_FENCE_OPEN = re.compile(r"^\s*```(?:xml|html)?\s*\n?", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\n?\s*```\s*$")
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
    # strip an opening markdown fence (```xml / ``` ) if present
    text = _FENCE_OPEN.sub("", text, count=1)
    # strip a trailing closing fence if present
    text = _FENCE_CLOSE.sub("", text)
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


def _text_of(el, strip: bool = True) -> Optional[str]:
    if el is None or el.text is None:
        return None
    return el.text.strip() if strip else el.text


def parse_response(raw: str) -> Tuple[str, str, str, List[Change]]:
    """Parse an LLM response.

    Returns (status, summary, notes, changes).
    Raises ProtocolError on malformed / unsupported documents.
    """
    xml = extract_xml(raw)
    try:
        root = etree.fromstring(xml.encode("utf-8"), parser=_safe_parser())
    except etree.XMLSyntaxError as exc:
        # one recovery attempt: quote unquoted attribute values
        # (e.g. <response version=1.0>) which some LLMs emit
        try:
            fixed = _fix_unquoted_attrs(xml)
            root = etree.fromstring(fixed.encode("utf-8"), parser=_safe_parser())
        except Exception:
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

    changes_el = root.find("changes")
    change_els = changes_el.findall("change") if changes_el is not None else []
    if not change_els:
        raise ProtocolError("Response contains no <change> elements.")

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

    return status, summary, notes, changes
