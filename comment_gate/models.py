"""The models Codex offers this account, read from the catalogue the Codex CLI
caches in its home directory. Every reader fails soft to an empty list."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any


class Model:
    def __init__(self, slug: str, name: str, description: str, efforts: dict[str, str]) -> None:
        self.slug, self.name, self.description = slug, name, description
        # Supported reasoning efforts, in order, mapped to Codex's description of each.
        self.efforts = efforts


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def cache_path() -> Path:
    return codex_home() / "models_cache.json"


def _priority(entry: dict[str, Any]) -> int:
    p = entry.get("priority")
    return p if isinstance(p, int) else 0


def load() -> list[Model]:
    """Models Codex lists in its own picker, in Codex's order."""
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        entries: list[Any] = data["models"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    models: list[Model] = []
    for m in sorted((m for m in entries if isinstance(m, dict)), key=_priority):
        if m.get("visibility", "list") != "list" or not isinstance(m.get("slug"), str):
            continue
        efforts = {r["effort"]: r.get("description") or "" for r in m.get("supported_reasoning_levels") or ()
                   if isinstance(r, dict) and isinstance(r.get("effort"), str)}
        models.append(Model(m["slug"], m.get("display_name") or m["slug"], m.get("description") or "", efforts))
    return models


def _subsequence(query: str, text: str) -> bool:
    it = iter(text)
    return all(ch in it for ch in query)


def search(models: Sequence[Model], query: str) -> list[Model]:
    """Models matching `query`: prefixes first, then substrings, then
    subsequences ("62a" finds gpt-6.2-astra), each in Codex's order."""
    q = query.strip().lower()
    if not q:
        return list(models)
    ranked: list[tuple[int, int, Model]] = []
    for i, m in enumerate(models):
        slug, name = m.slug.lower(), m.name.lower()
        if slug.startswith(q) or name.startswith(q):
            rank = 0
        elif q in slug or q in name:
            rank = 1
        elif _subsequence(q, slug):
            rank = 2
        else:
            continue
        ranked.append((rank, i, m))
    return [m for _, _, m in sorted(ranked, key=lambda r: r[:2])]
