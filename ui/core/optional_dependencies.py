"""Status helpers for optional application features."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class OptionalFeatureStatus:
    available: bool
    detail: str
    install_command: str
    languages: Tuple[str, ...] = ()


_TREE_SITTER_STATUS = None
TREE_SITTER_INSTALL_COMMAND = "python -m pip install -r requirements-symbols.txt"


def tree_sitter_status(refresh: bool = False) -> OptionalFeatureStatus:
    """Return whether at least one supported Tree-Sitter grammar actually works.

    The check is cached for the process lifetime; pass refresh=True after
    installing optional packages from inside the running application.
    """
    global _TREE_SITTER_STATUS
    if _TREE_SITTER_STATUS is not None and not refresh:
        return _TREE_SITTER_STATUS

    try:
        import tree_sitter  # noqa: F401
    except Exception as exc:
        _TREE_SITTER_STATUS = OptionalFeatureStatus(
            False,
            f"Tree-Sitter core package is unavailable ({type(exc).__name__}: {exc}).",
            TREE_SITTER_INSTALL_COMMAND,
            (),
        )
        return _TREE_SITTER_STATUS

    try:
        from core.tree_sitter_engine import TreeSitterEngine
        engine = TreeSitterEngine()
        languages = tuple(engine.available_languages())
    except Exception as exc:
        _TREE_SITTER_STATUS = OptionalFeatureStatus(
            False,
            f"Tree-Sitter could not initialize ({type(exc).__name__}: {exc}).",
            TREE_SITTER_INSTALL_COMMAND,
            (),
        )
        return _TREE_SITTER_STATUS

    if not languages:
        detail = "Tree-Sitter is installed, but no supported language grammar is available."
        _TREE_SITTER_STATUS = OptionalFeatureStatus(
            False, detail, TREE_SITTER_INSTALL_COMMAND, ())
    else:
        _TREE_SITTER_STATUS = OptionalFeatureStatus(
            True, "Available grammars: " + ", ".join(languages),
            TREE_SITTER_INSTALL_COMMAND, languages)
    return _TREE_SITTER_STATUS


def reset_optional_dependency_cache_for_tests() -> None:
    global _TREE_SITTER_STATUS
    _TREE_SITTER_STATUS = None
