"""Optional tree-sitter symbol + reference extraction (multi-language).

Replaces the regex heuristics in ``ingest.parse_generic`` for non-Python code
when ``tree-sitter-language-pack`` is installed, yielding real definitions
(functions/classes/methods) AND references (call sites). The references let
ingestion add ``references`` edges to the graph — the signal Personalized
PageRank needs to rank structurally.

Everything is defensive: a missing package, a grammar that fails to download, an
unsupported language, a bad query, or a parse error all resolve to ``None`` so
the caller falls back to the regex path. Ingestion never breaks because of this
module.

API verified against tree-sitter 0.26 / tree-sitter-language-pack 1.12:
``ts.Parser(lang)`` -> ``parser.parse(bytes)`` -> ``ts.QueryCursor(ts.Query(lang,
src)).captures(root)`` returning ``{capture_name: [node, ...]}``.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

try:  # pragma: no cover - import guard
    import tree_sitter as _ts
    from tree_sitter_language_pack import get_language as _get_language
    _AVAILABLE = True
except Exception:  # pragma: no cover
    _ts = None
    _get_language = None
    _AVAILABLE = False

# Bounds so a pathological file can't blow up ingest.
_MAX_DEFS = 300
_MAX_REFS = 800

# ext -> tree-sitter language name.
_LANG_BY_EXT = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
}

# Capture names are read structurally: "def.<kind>" => definition, "ref.*" =>
# reference (call site). A query that names a node type the grammar lacks fails
# to compile and that language is disabled (fallback), so a wrong guess is safe.
_QUERIES = {
    "javascript": """
        (function_declaration name:(identifier) @def.function)
        (class_declaration name:(identifier) @def.class)
        (method_definition name:(property_identifier) @def.method)
        (call_expression function:(identifier) @ref.call)
    """,
    "typescript": """
        (function_declaration name:(identifier) @def.function)
        (class_declaration name:(type_identifier) @def.class)
        (method_definition name:(property_identifier) @def.method)
        (interface_declaration name:(type_identifier) @def.interface)
        (call_expression function:(identifier) @ref.call)
    """,
    "tsx": """
        (function_declaration name:(identifier) @def.function)
        (class_declaration name:(type_identifier) @def.class)
        (method_definition name:(property_identifier) @def.method)
        (call_expression function:(identifier) @ref.call)
    """,
    "go": """
        (function_declaration name:(identifier) @def.function)
        (method_declaration name:(field_identifier) @def.method)
        (type_spec name:(type_identifier) @def.type)
        (call_expression function:(identifier) @ref.call)
    """,
    "rust": """
        (function_item name:(identifier) @def.function)
        (struct_item name:(type_identifier) @def.struct)
        (enum_item name:(type_identifier) @def.enum)
        (call_expression function:(identifier) @ref.call)
    """,
    "java": """
        (method_declaration name:(identifier) @def.method)
        (class_declaration name:(identifier) @def.class)
        (method_invocation name:(identifier) @ref.call)
    """,
    "ruby": """
        (method name:(identifier) @def.method)
        (class name:(constant) @def.class)
        (call method:(identifier) @ref.call)
    """,
    "c": """
        (function_definition declarator:(function_declarator declarator:(identifier) @def.function))
        (call_expression function:(identifier) @ref.call)
    """,
    "cpp": """
        (function_definition declarator:(function_declarator declarator:(identifier) @def.function))
        (class_specifier name:(type_identifier) @def.class)
        (call_expression function:(identifier) @ref.call)
    """,
}


class Extraction(NamedTuple):
    defs: list[tuple[str, str, int]]   # (name, kind, line)
    refs: list[str]                    # referenced identifier names


# Cache of (parser, query) per language; None marks a language we failed to build.
_CACHE: dict[str, Optional[tuple]] = {}


def available() -> bool:
    return _AVAILABLE


def _build(lang_name: str):
    if lang_name in _CACHE:
        return _CACHE[lang_name]
    result = None
    try:
        lang = _get_language(lang_name)          # may download the grammar
        parser = _ts.Parser(lang)
        query = _ts.Query(lang, _QUERIES[lang_name])
        result = (parser, query)
    except Exception:
        result = None
    _CACHE[lang_name] = result
    return result


def extract(ext: str, source) -> Optional[Extraction]:
    """Return defs+refs for a source string/bytes, or None to fall back."""
    if not _AVAILABLE:
        return None
    lang_name = _LANG_BY_EXT.get(ext.lower())
    if not lang_name or lang_name not in _QUERIES:
        return None
    built = _build(lang_name)
    if built is None:
        return None
    parser, query = built
    try:
        data = source.encode("utf-8", "replace") if isinstance(source, str) else source
        tree = parser.parse(data)
        captures = _ts.QueryCursor(query).captures(tree.root_node)
    except Exception:
        return None

    defs: list[tuple[str, str, int]] = []
    refs: list[str] = []
    seen_def: set[tuple[str, int]] = set()
    seen_ref: set[str] = set()
    for cap_name, nodes in captures.items():
        is_def = cap_name.startswith("def")
        kind = cap_name.split(".", 1)[1] if (is_def and "." in cap_name) else "symbol"
        for n in nodes:
            try:
                text = n.text.decode("utf-8", "replace")
            except Exception:
                continue
            if not text:
                continue
            if is_def:
                key = (text, n.start_point[0] + 1)
                if key in seen_def or len(defs) >= _MAX_DEFS:
                    continue
                seen_def.add(key)
                defs.append((text, kind, n.start_point[0] + 1))
            elif cap_name.startswith("ref"):
                if text in seen_ref or len(refs) >= _MAX_REFS:
                    continue
                seen_ref.add(text)
                refs.append(text)
    if not defs and not refs:
        return None
    return Extraction(defs, refs)
