"""Markdown knowledge base for the chatbot.

Everything the model needs to know about the data — what each view holds, what each short code
means, rules about dates, worked example queries — lives as Markdown files under ``knowledge/``
(repo root, see ``knowledge/README.md``), not as strings in code. They are loaded and validated once
at startup (``load_knowledge`` in the app lifespan) and assembled into the prompt per question.

File format: a front-matter block, then the Markdown body::

    ---
    id: glossary.formula-builder        # unique, <category>.<name>
    title: Formula Builder codes
    category: glossary                  # data-source | rules | glossary | examples
    load: always                        # always | on-demand
    triggers: [gap, unfilled]           # on-demand only: terms in the question that pull this doc in
    order: 20                           # sort within its category (default 100)
    ---

``always`` docs go into every prompt. ``on-demand`` docs are included only when one of their
``triggers`` appears (case-insensitive, whole-token) in the question or the recent user turns, so the
prompt stays small as the knowledge base grows. If the total exceeds ``chat_knowledge_max_chars``
on-demand docs are dropped (lowest priority first) before anything ``always`` is touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# category -> (render order, heading used in the prompt)
CATEGORIES: dict[str, tuple[int, str]] = {
    "data-source": (1, "DATA SOURCES"),
    "rules": (2, "DATA RULES"),
    "glossary": (3, "GLOSSARY OF SHORT CODES"),
    "examples": (4, "EXAMPLES (question -> sql)"),
}
LOAD_MODES = {"always", "on-demand"}
_REQUIRED = ("id", "title", "category", "load")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SKIP_FILES = {"readme.md"}


class KnowledgeError(Exception):
    """A knowledge file is malformed (bad front-matter, duplicate id, ...)."""


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    category: str
    load: str
    body: str
    path: str
    triggers: tuple[str, ...] = ()
    order: int = 100

    @property
    def size(self) -> int:
        return len(self.body)


def _parse_value(raw: str):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()] if inner else []
    return raw.strip("'\"")


def parse_doc(text: str, path: str = "<memory>") -> Doc:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise KnowledgeError(f"{path}: must start with a '---' front-matter block")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise KnowledgeError(f"{path}: front-matter block is not closed with '---'") from None
    meta: dict = {}
    for ln in lines[1:end]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ":" not in ln:
            raise KnowledgeError(f"{path}: bad front-matter line {ln!r}")
        key, _, val = ln.partition(":")
        # allow trailing "# comment" after a value
        val = re.sub(r"\s+#.*$", "", val)
        meta[key.strip()] = _parse_value(val)
    missing = [k for k in _REQUIRED if not meta.get(k)]
    if missing:
        raise KnowledgeError(f"{path}: missing front-matter field(s): {', '.join(missing)}")
    if not _ID_RE.match(meta["id"]):
        raise KnowledgeError(f"{path}: id {meta['id']!r} must be lowercase letters/digits/./-/_")
    if meta["category"] not in CATEGORIES:
        raise KnowledgeError(f"{path}: category {meta['category']!r} not in {sorted(CATEGORIES)}")
    if not meta["id"].startswith(meta["category"] + "."):
        raise KnowledgeError(f"{path}: id {meta['id']!r} must start with '{meta['category']}.'")
    if meta["load"] not in LOAD_MODES:
        raise KnowledgeError(f"{path}: load must be one of {sorted(LOAD_MODES)}")
    triggers = meta.get("triggers", [])
    if isinstance(triggers, str):
        triggers = [triggers]
    if meta["load"] == "on-demand" and not triggers:
        raise KnowledgeError(f"{path}: an on-demand doc needs at least one trigger")
    try:
        order = int(meta.get("order", 100))
    except ValueError:
        raise KnowledgeError(f"{path}: order must be an integer") from None
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise KnowledgeError(f"{path}: empty body")
    return Doc(
        id=meta["id"], title=meta["title"], category=meta["category"], load=meta["load"],
        body=body, path=path, triggers=tuple(t for t in triggers if t), order=order,
    )


def _trigger_regex(term: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term.strip()) + r"(?![A-Za-z0-9])", re.IGNORECASE)


@dataclass
class KnowledgeBase:
    docs: dict[str, Doc] = field(default_factory=dict)
    _patterns: dict[str, list[re.Pattern]] = field(default_factory=dict, repr=False)

    def add(self, doc: Doc) -> None:
        if doc.id in self.docs:
            raise KnowledgeError(f"duplicate id {doc.id!r}: {self.docs[doc.id].path} and {doc.path}")
        self.docs[doc.id] = doc
        self._patterns[doc.id] = [_trigger_regex(t) for t in doc.triggers]

    def __len__(self) -> int:
        return len(self.docs)

    def triggered(self, text: str) -> set[str]:
        return {i for i, pats in self._patterns.items() if any(p.search(text) for p in pats)}

    def select(self, text: str, *, max_chars: int | None = None) -> list[Doc]:
        """Docs to put in the prompt for *text* (the question plus recent user turns)."""
        hit = self.triggered(text)
        chosen = [d for d in self.docs.values() if d.load == "always" or d.id in hit]
        limit = settings.chat_knowledge_max_chars if max_chars is None else max_chars
        total = sum(d.size for d in chosen)
        if total > limit:
            # Drop on-demand docs, largest first, until it fits; 'always' docs are never dropped.
            for d in sorted((d for d in chosen if d.load == "on-demand"), key=lambda d: -d.size):
                if total <= limit:
                    break
                chosen.remove(d)
                total -= d.size
                logger.warning("knowledge_doc_dropped_over_budget", doc=d.id, size=d.size)
        return sorted(chosen, key=lambda d: (CATEGORIES[d.category][0], d.order, d.id))

    def render(self, docs: list[Doc]) -> str:
        out: list[str] = []
        current = None
        for d in docs:
            if d.category != current:
                current = d.category
                out.append(f"\n=== {CATEGORIES[current][1]} ===")
            out.append(f"\n## {d.title}\n{d.body}")
        return "\n".join(out).strip()

    def stats(self) -> dict:
        by_cat: dict[str, int] = {}
        for d in self.docs.values():
            by_cat[d.category] = by_cat.get(d.category, 0) + 1
        return {
            "docs": len(self.docs),
            "always": sum(1 for d in self.docs.values() if d.load == "always"),
            "on_demand": sum(1 for d in self.docs.values() if d.load == "on-demand"),
            "chars": sum(d.size for d in self.docs.values()),
            "by_category": by_cat,
        }


def load_knowledge(directory: str | Path | None = None) -> KnowledgeBase:
    """Read every ``*.md`` under *directory* (recursively; README.md files are skipped)."""
    root = Path(directory or settings.chat_knowledge_dir)
    if not root.is_absolute() and not root.exists():
        # Relative paths resolve against the app root (the folder holding ``knowledge/``).
        root = Path(__file__).resolve().parents[3] / root
    if not root.is_dir():
        raise KnowledgeError(f"knowledge directory not found: {root}")
    kb = KnowledgeBase()
    files = sorted(p for p in root.rglob("*.md") if p.name.lower() not in _SKIP_FILES)
    if not files:
        raise KnowledgeError(f"no knowledge files under {root}")
    for path in files:
        kb.add(parse_doc(path.read_text(encoding="utf-8"), str(path.relative_to(root))))
    return kb


_kb: KnowledgeBase | None = None


def init_knowledge() -> KnowledgeBase | None:
    """Load at startup. A broken file must not take the whole API down: log loudly and leave the
    chatbot reporting itself unavailable until it is fixed (CI test_knowledge_base catches it first)."""
    global _kb
    try:
        _kb = load_knowledge()
        logger.info("knowledge_base_loaded", **_kb.stats())
    except KnowledgeError as exc:
        _kb = None
        logger.error("knowledge_base_load_failed", error=str(exc))
    return _kb


def get_knowledge_base() -> KnowledgeBase:
    """The loaded KB (lazy-loads once so tests / scripts work without the app lifespan)."""
    global _kb
    if _kb is None:
        _kb = load_knowledge()
    return _kb
