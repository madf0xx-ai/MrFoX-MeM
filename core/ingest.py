"""Ingestion: walk a directory, build a knowledge tree of nodes + edges.

Security model (enforced here, not bolted on):
  * Path must be absolute, exist, be a directory; no symlink escape outside root.
  * Never import or exec project code. Python is parsed statically via ``ast``.
  * Skip binaries, enforce max-file-size and max-files caps (DoS bounds).
  * Secret-scan & redact: .env / *.pem / id_rsa / private keys / AWS keys are
    never stored raw; their content is replaced with a redaction marker.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
import re
import sys
import uuid
from typing import Optional

from . import embed as embed_mod
from . import treesit
from .store import Store

# ---------------------------------------------------------------------------
# Caps & limits
# ---------------------------------------------------------------------------
MAX_FILE_SIZE = 1_000_000  # 1 MB
MAX_FILES = 5_000
MAX_SUMMARY_CHARS = 600
EMBED_TEXT_CHARS = 2_000
# Chars of file BODY folded into the embedding text so the vector reflects
# content, not just the path + summary (the embedding model truncates to its own
# token limit anyway; this just stops the vector being name-only).
EMBED_CONTENT_CHARS = 1_500

# Reference-edge resolution guards. A bare identifier defined in many files (or a
# ubiquitous short name) can't be resolved to a single definer by name alone —
# linking every caller to an arbitrary first definer injects false edges that
# distort the PageRank graph (edges weighted 3.0). So we skip those.
_MAX_REF_DEF_FILES = 3
_COMMON_IDENTS = frozenset({
    "run", "get", "set", "add", "put", "pop", "map", "main", "init", "node",
    "name", "self", "data", "value", "values", "result", "results", "item",
    "items", "key", "keys", "load", "save", "read", "write", "open", "close",
    "start", "stop", "next", "call", "send", "recv", "list", "dict", "str",
    "int", "len", "new", "make", "build", "parse", "print", "log", "test",
})

DEFAULT_EXCLUDES = [
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".mypy_cache", ".pytest_cache", "dist", "build", ".idea",
    ".vscode", "*.lock", ".DS_Store", ".tox", ".cache", "site-packages",
]

# ---------------------------------------------------------------------------
# Secret scanning
# ---------------------------------------------------------------------------
SECRET_FILENAME_PATTERNS = [
    ".env", ".env.*", "*.env", "*.pem", "*.key", "id_rsa", "id_dsa", "id_ecdsa",
    "id_ed25519", "*.p12", "*.pfx", ".npmrc", ".netrc", "credentials",
    "*.pgpass", ".htpasswd", "*.keystore", "*.jks", "secrets.*", "*.ovpn",
]

SECRET_CONTENT_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                         # AWS access key id
    re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[=:]\s*['\"]?[^\s'\"]{6,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),               # GitHub tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),             # Slack tokens
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),                   # Google API key
    re.compile(r"(?:sk|rk)_live_[0-9A-Za-z]{16,}"),          # Stripe live key
    # creds in URL. Possessive `*+` (3.11+) forbids backtracking so a long run of
    # [A-Za-z0-9+.-] with no "://" cannot amplify matching cost (ReDoS-safe).
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*+://[^/\s:@]+:[^/\s:@]+@"),
]

# Bound how much text the (potentially superlinear) secret regexes ever scan, so
# a large sub-cap file cannot spin CPU for minutes while the ingest transaction
# holds the store lock (a DoS the 1 MB file cap alone does not prevent).
SECRET_SCAN_MAX_CHARS = 512_000

REDACTION = "[REDACTED: secret content not stored]"

# ---------------------------------------------------------------------------
# Extension -> node kind / language
# ---------------------------------------------------------------------------
CODE_EXTS = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp",
    ".hpp": "cpp", ".rb": "ruby", ".php": "php", ".cs": "csharp",
    ".swift": "swift", ".kt": "kotlin", ".scala": "scala", ".sh": "shell",
    ".lua": "lua", ".r": "r", ".m": "objc",
}
DOC_EXTS = {".md", ".markdown", ".rst", ".txt", ".adoc"}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf",
    ".zip", ".gz", ".tar", ".tgz", ".bz2", ".7z", ".rar", ".exe", ".dll",
    ".so", ".dylib", ".o", ".a", ".class", ".jar", ".pyc", ".woff", ".woff2",
    ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg",
    ".bin", ".dat", ".db", ".sqlite", ".wasm", ".node",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip().lower()).strip("-")
    return s or "project"


def _matches_filename_secret(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, pat) for pat in SECRET_FILENAME_PATTERNS)


def _scan_secret_content(text: str) -> bool:
    # Cap total scanned text and drop pathologically long lines (mirroring the
    # symbol-regex guard) before running the secret patterns — bounds worst-case
    # cost regardless of file contents.
    if len(text) > SECRET_SCAN_MAX_CHARS:
        text = text[:SECRET_SCAN_MAX_CHARS]
    lines = text.splitlines()
    if any(len(line) > _MAX_LINE_FOR_REGEX for line in lines):
        text = "\n".join(line for line in lines if len(line) <= _MAX_LINE_FOR_REGEX)
    return any(p.search(text) for p in SECRET_CONTENT_PATTERNS)


def _is_excluded(name: str, rel_parts: list[str], excludes: list[str]) -> bool:
    for pat in excludes:
        if fnmatch.fnmatch(name, pat):
            return True
        if name == pat or pat in rel_parts:
            return True
    return False


def _looks_binary(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXTS:
        return True
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(4096)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    # High ratio of non-text bytes -> binary.
    if chunk:
        text_chars = sum(
            1 for b in chunk if b in (9, 10, 13) or 32 <= b <= 126 or b >= 128
        )
        if text_chars / len(chunk) < 0.7:
            return True
    return False


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def _clip(text: str, n: int = MAX_SUMMARY_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _embed_text(*parts: str) -> str:
    """Join non-empty parts into the text that gets embedded for a node.

    We prepend structural context (the relative path / kind / signature) before
    the summary — a zero-LLM approximation of Anthropic's Contextual Retrieval:
    an isolated symbol name embeds poorly, but "core/tree.py :: hybrid_search"
    situates it, which measurably improves retrieval.
    """
    return "\n".join(p.strip() for p in parts if p and p.strip())


def summarize_doc(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return _clip(s.lstrip("#").strip())
    for line in text.splitlines():
        if line.strip():
            return _clip(line.strip())
    return ""


def summarize_generic(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return _clip(" ".join(lines[:3]))


# ---------------------------------------------------------------------------
# Python static analysis (NEVER import/exec)
# ---------------------------------------------------------------------------
class PySymbol:
    """A top-level function or class extracted from a Python module via AST."""

    def __init__(self, name: str, kind: str, lineno: int, summary: str):
        self.name = name
        self.kind = kind  # function | class
        self.lineno = lineno
        self.summary = summary


def parse_python(text: str) -> tuple[str, list[PySymbol], list[str]]:
    """Return (module_summary, top_level_symbols, imported_module_names)."""
    try:
        tree = ast.parse(text)
    except Exception:
        # Best-effort static parse: a crafted/huge file can raise RecursionError
        # or MemoryError, not just SyntaxError. One bad file must never abort the
        # whole (single-transaction) ingest.
        return summarize_generic(text), [], []

    module_doc = ast.get_docstring(tree) or ""
    module_summary = _clip(module_doc) if module_doc else summarize_generic(text)

    symbols: list[PySymbol] = []
    imports: set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ""
            symbols.append(
                PySymbol(node.name, "function", node.lineno, _clip(doc))
            )
        elif isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node) or ""
            symbols.append(
                PySymbol(node.name, "class", node.lineno, _clip(doc))
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                imports.add(node.module.split(".")[0])

    return module_summary, symbols, sorted(imports)


def python_call_refs(text: str) -> list[str]:
    """Distinct names of functions/methods called in a Python module.

    Feeds the ``references`` edges (referencer file -> definer file) that the
    graph-PageRank pass ranks over. Static AST walk only — never executes code.
    """
    try:
        tree = ast.parse(text)
    except Exception:
        # See parse_python: tolerate RecursionError/MemoryError from crafted files.
        return []
    names: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else None
            )
            if name and name not in seen:
                seen.add(name)
                names.append(name)
                if len(names) >= 500:
                    break
    return names


# ---------------------------------------------------------------------------
# Generic (non-python) symbol extraction via heuristics
# ---------------------------------------------------------------------------
_SYMBOL_REGEXES = [
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", re.M),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),       # Go
    re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)", re.M),               # Rust
    re.compile(r"^\s*def\s+([A-Za-z_]\w*)", re.M),                          # Ruby
    # Java/C-ish. Atomic groups (?>...) (Python 3.11+) prevent catastrophic
    # backtracking on long whitespace/token runs (ReDoS-safe).
    re.compile(
        r"^[ \t]*(?>(?:public|private|protected|static|final|synchronized)[ \t]+)*"
        r"(?>[A-Za-z_][\w<>\[\].]*[ \t]+)+([A-Za-z_]\w*)[ \t]*\(",
        re.M,
    ),
]

# Lines longer than this are skipped by the heuristic regex pass — a single
# pathological minified/space-padded line cannot amplify matching cost (DoS).
_MAX_LINE_FOR_REGEX = 2_000
_IMPORT_REGEXES = [
    re.compile(r"""^\s*import\s+.*?from\s+['"]([^'"]+)['"]""", re.M),
    re.compile(r"""^\s*require\(['"]([^'"]+)['"]\)""", re.M),
    re.compile(r"""^\s*#include\s+[<"]([^>"]+)[>"]""", re.M),
]


def parse_generic(text: str) -> tuple[list[str], list[str]]:
    """Return (symbol_names, imported_targets) via regex heuristics."""
    # Drop pathologically long lines before regex matching (ReDoS/DoS bound).
    if any(len(line) > _MAX_LINE_FOR_REGEX for line in text.splitlines()):
        text = "\n".join(
            line for line in text.splitlines() if len(line) <= _MAX_LINE_FOR_REGEX
        )
    symbols: list[str] = []
    seen: set[str] = set()
    for rx in _SYMBOL_REGEXES:
        for m in rx.finditer(text):
            name = m.group(1)
            if name and name not in seen and name not in {"if", "for", "while", "switch", "return"}:
                seen.add(name)
                symbols.append(name)
            if len(symbols) >= 50:
                break
    imports: list[str] = []
    iseen: set[str] = set()
    for rx in _IMPORT_REGEXES:
        for m in rx.finditer(text):
            t = m.group(1)
            if t and t not in iseen:
                iseen.add(t)
                imports.append(t)
    return symbols, imports


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------
def _denied_roots() -> list[str]:
    """Sensitive directories that must never be ingested (credential/system).

    Platform-aware: a common set of user credential stores under the home
    directory (all OSes) plus OS-specific system/credential roots. Paths are
    always built with ``os.path.join`` (never literal ``/``) so they are correct
    on POSIX and Windows alike.
    """
    home = os.path.expanduser("~")
    # User credential stores under home — applies to all OSes (covers Windows
    # %USERPROFILE%\.ssh, .aws, etc. since expanduser resolves there too).
    home_names = [
        ".ssh", ".aws", ".gnupg", os.path.join(".config", "gcloud"),
        ".kube", ".docker", ".azure", ".netrc", ".password-store",
    ]
    roots = [os.path.realpath(os.path.join(home, n)) for n in home_names]

    if sys.platform == "darwin":
        roots.append(os.path.realpath(os.path.join(home, "Library", "Keychains")))
        roots += [os.path.realpath(p) for p in ("/etc", "/private/etc", "/var/root")]
    elif os.name == "nt":
        windir = os.environ.get("SystemRoot") or "C:\\Windows"
        roots.append(os.path.realpath(windir))
        # Windows credential / DPAPI / vault stores (parity with the macOS
        # Keychain + Linux /etc denials): Credentials, DPAPI master keys
        # (Protect), Crypto keys, and the Credential Vault.
        for env_key in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(env_key)
            if base:
                roots += [
                    os.path.realpath(os.path.join(base, "Microsoft", n))
                    for n in ("Credentials", "Protect", "Crypto", "Vault")
                ]
    else:
        # Linux / other POSIX.
        roots += [
            os.path.realpath(p)
            for p in ("/etc", "/root", "/proc", "/sys", "/boot")
        ]
    return roots


def _allowed_roots() -> Optional[list[str]]:
    """Optional allow-list from env MRFOX_ALLOWED_ROOTS (os.pathsep-separated).

    When set, an ingest path must resolve to within one of these roots.
    When unset, ingestion is allowed anywhere except the denied roots below.
    """
    raw = os.environ.get("MRFOX_ALLOWED_ROOTS", "").strip()
    if not raw:
        return None
    return [os.path.realpath(p) for p in raw.split(os.pathsep) if p.strip()]


def _within(child: str, parent: str) -> bool:
    """True if ``child`` is ``parent`` or a descendant (no sibling-prefix bug).

    Comparison goes through ``os.path.normcase`` so it is case-insensitive on
    Windows (where paths compare case-insensitively) and a no-op on POSIX —
    POSIX case-sensitivity and existing macOS behavior are unchanged.
    """
    child = os.path.normcase(child)
    parent = os.path.normcase(parent).rstrip(os.sep) or os.sep
    return child == parent or child.startswith(parent + os.sep)


def validate_ingest_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not os.path.isabs(path):
        raise ValueError("path must be absolute")
    if os.path.islink(path):
        raise ValueError("path must not be a symlink")
    real = os.path.realpath(path)
    if not os.path.exists(real):
        raise ValueError("path does not exist")
    if not os.path.isdir(real):
        raise ValueError("path must be a directory")

    # Never ingest the filesystem root or a bare home directory wholesale.
    # normcase keeps the home compare case-insensitive on Windows (no-op POSIX).
    home = os.path.realpath(os.path.expanduser("~"))
    real_nc = os.path.normcase(real)
    if real_nc == os.path.normcase(os.sep) or real_nc == os.path.normcase(home):
        raise ValueError("refusing to ingest filesystem root or home directory")

    # Hard denylist: credential / system directories.
    for denied in _denied_roots():
        if _within(real, denied):
            raise ValueError("path is within a protected/sensitive directory")

    # Optional allow-list confinement.
    allowed = _allowed_roots()
    if allowed is not None and not any(_within(real, a) for a in allowed):
        raise ValueError("path is outside the configured MRFOX_ALLOWED_ROOTS")

    return real


# ---------------------------------------------------------------------------
# Main ingest
# ---------------------------------------------------------------------------
class IngestResult:
    """Counters summarizing what one ingest run stored (nodes, edges, files)."""

    def __init__(self) -> None:
        self.project = ""
        self.nodes = 0
        self.edges = 0
        self.files = 0
        self.skipped = 0
        self.reused = 0  # embeddings reused from cache (incremental re-index)


def ingest(
    store: Store,
    path: str,
    project: Optional[str] = None,
    exclude: Optional[list[str]] = None,
) -> IngestResult:
    """Ingest ``path`` into ``project`` as one atomic, single-commit transaction.

    Wrapping the whole walk in ``store.bulk()`` collapses what was thousands of
    per-row commits (blob + node + edge + embedding, per file and per symbol)
    into a single commit — the dominant ingest speed win — and makes re-ingest
    atomic: a failure part-way rolls back instead of leaving a half-built tree.
    """
    with store.bulk():
        return _ingest_impl(store, path, project=project, exclude=exclude)


def _ingest_impl(
    store: Store,
    path: str,
    project: Optional[str] = None,
    exclude: Optional[list[str]] = None,
) -> IngestResult:
    root = validate_ingest_path(path)
    project_name = slugify(project) if project else slugify(os.path.basename(root))
    excludes = list(DEFAULT_EXCLUDES) + list(exclude or [])

    embedder = embed_mod.get_embedder()
    result = IngestResult()
    result.project = project_name

    # Idempotent: re-ingest replaces project rows.
    store.clear_project(project_name)
    store.upsert_project(project_name, project_name, root)

    # dir_id maps an absolute dir path -> node id.
    root_id = _new_id("dir")
    dir_ids: dict[str, str] = {root: root_id}
    store.insert_node(
        root_id, project_name, "dir", os.path.basename(root) or root,
        root, None, "Project root", None, os.path.basename(root),
    )
    result.nodes += 1

    # Collect embedding work: (node_id, text)
    embed_batch: list[tuple[str, str]] = []
    # module path -> module node id, for resolving Python imports later.
    module_by_name: dict[str, str] = {}
    pending_imports: list[tuple[str, str, list[str]]] = []  # (module_node_id, lang, import_names)
    # symbol name -> defining file node id, and per-file referenced names, so we
    # can add referencer->definer `references` edges after the walk.
    symbol_def_file: dict[str, str] = {}
    symbol_def_count: dict[str, int] = {}  # name -> how many files define it
    pending_refs: list[tuple[str, list[str]]] = []  # (file_node_id, referenced_names)

    file_count = 0
    stop = False

    for current, dirnames, filenames in os.walk(root, followlinks=False):
        if stop:
            break
        # Stay inside root (defense against any symlinked dir).
        if os.path.realpath(current) != current and not _within(os.path.realpath(current), root):
            dirnames[:] = []
            continue

        rel_parts = os.path.relpath(current, root).split(os.sep) if current != root else []
        # Prune excluded / symlinked dirs in place.
        kept = []
        for d in sorted(dirnames):
            full = os.path.join(current, d)
            if os.path.islink(full):
                result.skipped += 1
                continue
            if _is_excluded(d, rel_parts, excludes):
                continue
            kept.append(d)
        dirnames[:] = kept

        parent_id = dir_ids.get(current, root_id)

        # Create dir nodes for kept subdirs.
        for d in dirnames:
            full = os.path.join(current, d)
            did = _new_id("dir")
            dir_ids[full] = did
            store.insert_node(
                did, project_name, "dir", d, full, parent_id, "", None, d
            )
            result.nodes += 1
            eid = _new_id("edge")
            store.insert_edge(eid, project_name, parent_id, did, "contains")
            result.edges += 1

        for fname in sorted(filenames):
            if file_count >= MAX_FILES:
                stop = True
                break
            full = os.path.join(current, fname)
            if os.path.islink(full):
                result.skipped += 1
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                result.skipped += 1
                continue
            if size > MAX_FILE_SIZE:
                result.skipped += 1
                continue

            ext = os.path.splitext(fname)[1].lower()
            is_secret_name = _matches_filename_secret(fname)

            if not is_secret_name and _looks_binary(full):
                result.skipped += 1
                continue

            text = "" if is_secret_name else (_read_text(full) or "")
            redacted = False
            if is_secret_name or _scan_secret_content(text):
                redacted = True
                text = ""  # never store raw secret content

            file_count += 1
            result.files += 1

            fkind = "doc" if ext in DOC_EXTS else (
                "module" if ext in CODE_EXTS else "file"
            )

            # Build summary + content hash (redacted files store the marker).
            if redacted:
                summary = REDACTION
                blob_text = REDACTION
            elif ext == ".py":
                msum, _syms, _imps = parse_python(text)
                summary = msum or "Python module"
                blob_text = text
            elif ext in DOC_EXTS:
                summary = summarize_doc(text) or "Document"
                blob_text = text
            else:
                summary = summarize_generic(text)
                blob_text = text

            content_hash = store.put_blob(blob_text.encode("utf-8"))
            file_id = _new_id("file")
            store.insert_node(
                file_id, project_name, fkind, fname, full, parent_id,
                _clip(summary), content_hash, blob_text[:EMBED_TEXT_CHARS],
            )
            result.nodes += 1
            eid = _new_id("edge")
            store.insert_edge(eid, project_name, parent_id, file_id, "contains")
            result.edges += 1
            rel_path = os.path.relpath(full, root)
            embed_body = "" if redacted else blob_text[:EMBED_CONTENT_CHARS]
            embed_batch.append(
                (file_id, _embed_text(rel_path, f"{fkind}: {fname}", summary, embed_body))
            )

            if redacted:
                continue

            # Symbol + import extraction.
            if ext == ".py":
                _msum, syms, imps = parse_python(text)
                mod_name = os.path.splitext(fname)[0]
                module_by_name[mod_name] = file_id
                rel = os.path.relpath(full, root)
                module_by_name[rel.replace(os.sep, ".")[:-3]] = file_id
                for sym in syms:
                    sid = _new_id("sym")
                    store.insert_node(
                        sid, project_name, "symbol",
                        f"{sym.name} ({sym.kind})", full, file_id,
                        _clip(sym.summary), None, f"{sym.name} {sym.summary}",
                    )
                    result.nodes += 1
                    se = _new_id("edge")
                    store.insert_edge(se, project_name, file_id, sid, "contains")
                    result.edges += 1
                    symbol_def_file.setdefault(sym.name, file_id)
                    symbol_def_count[sym.name] = symbol_def_count.get(sym.name, 0) + 1
                    embed_batch.append(
                        (sid, _embed_text(f"{rel} :: {sym.name} ({sym.kind})", sym.summary))
                    )
                if imps:
                    pending_imports.append((file_id, "python", imps))
                py_refs = python_call_refs(text)
                if py_refs:
                    pending_refs.append((file_id, py_refs))
            elif ext in CODE_EXTS:
                rel = os.path.relpath(full, root)
                # Prefer tree-sitter (precise, multi-language) when available;
                # fall back to the regex heuristics otherwise. Imports still come
                # from the regex pass (tree-sitter path here extracts defs/refs).
                extraction = treesit.extract(ext, text)
                if extraction is not None:
                    for name, kind, _line in extraction.defs:
                        sid = _new_id("sym")
                        store.insert_node(
                            sid, project_name, "symbol",
                            f"{name} ({kind})", full, file_id, "", None, name,
                        )
                        result.nodes += 1
                        se = _new_id("edge")
                        store.insert_edge(se, project_name, file_id, sid, "contains")
                        result.edges += 1
                        symbol_def_file.setdefault(name, file_id)
                        symbol_def_count[name] = symbol_def_count.get(name, 0) + 1
                        embed_batch.append((sid, _embed_text(f"{rel} :: {name} ({kind})")))
                    if extraction.refs:
                        pending_refs.append((file_id, extraction.refs))
                    _syms, imps = parse_generic(text)
                else:
                    syms, imps = parse_generic(text)
                    for name in syms[:30]:
                        sid = _new_id("sym")
                        store.insert_node(
                            sid, project_name, "symbol", name, full, file_id,
                            "", None, name,
                        )
                        result.nodes += 1
                        se = _new_id("edge")
                        store.insert_edge(se, project_name, file_id, sid, "contains")
                        result.edges += 1
                        symbol_def_file.setdefault(name, file_id)
                        symbol_def_count[name] = symbol_def_count.get(name, 0) + 1
                        embed_batch.append((sid, _embed_text(f"{rel} :: {name}")))
                if imps:
                    pending_imports.append((file_id, CODE_EXTS[ext], imps))

    # Resolve imports -> edges (only to known intra-project modules).
    for module_id, lang, names in pending_imports:
        for name in names:
            target = module_by_name.get(name) or module_by_name.get(
                name.split(".")[0]
            )
            if target and target != module_id:
                ie = _new_id("edge")
                store.insert_edge(ie, project_name, module_id, target, "imports")
                result.edges += 1

    # Resolve references -> edges (referencer file -> definer file), for known
    # intra-project symbols only. Skip very short names (i, x, id, …) to avoid
    # noisy links, and dedup per (file, definer). These edges give the graph pass
    # its cross-file signal.
    for file_id, ref_names in pending_refs:
        linked: set[str] = set()
        for name in ref_names:
            if len(name) < 3 or name in _COMMON_IDENTS:
                continue
            if symbol_def_count.get(name, 0) > _MAX_REF_DEF_FILES:
                continue  # defined in too many files -> can't resolve by name; skip
            target = symbol_def_file.get(name)
            if target and target != file_id and target not in linked:
                linked.add(target)
                re_id = _new_id("edge")
                store.insert_edge(re_id, project_name, file_id, target, "references")
                result.edges += 1

    # Compute + persist embeddings, reusing cached vectors for unchanged content
    # (incremental re-index — the expensive embed step is skipped when a node's
    # embed-text is unchanged since a prior ingest). Cache key includes backend +
    # dim so a model change never reuses a stale vector.
    if embed_batch:
        texts = [t for _id, t in embed_batch]
        dim = embedder.dim
        prefix = f"{embedder.backend}:{dim}:"
        keys = [hashlib.sha256((prefix + t).encode("utf-8")).hexdigest() for t in texts]
        cache = store.get_cached_embeddings(keys)
        miss = [i for i, k in enumerate(keys) if k not in cache]
        if miss:
            fresh = embedder.embed([texts[i] for i in miss])
            for j, i in enumerate(miss):
                cache[keys[i]] = fresh[j]
                store.put_cached_embedding(keys[i], dim, fresh[j])
        for (nid, _t), k in zip(embed_batch, keys):
            store.put_embedding(nid, dim, cache[k])
        result.reused = len(texts) - len(miss)

    return result
