"""Workspace indexing and search.

Public API:
    get_indexer(config) -> BaseIndexer
    load_workspace_config() -> WorkspaceConfig
"""

from __future__ import annotations

import logging

from workspace.base import BaseIndexer
from workspace.config import WorkspaceConfig, load_workspace_config
from workspace.default import DefaultIndexer
from workspace.types import IndexingError, IndexSummary, SearchResult

log = logging.getLogger(__name__)


def get_indexer(config: WorkspaceConfig | None = None) -> BaseIndexer:
    if config is None:
        config = load_workspace_config()
    if config.indexer == "default":
        return DefaultIndexer(config)
    try:
        from plugins.workspace import load_workspace_indexer

        cls = load_workspace_indexer(config.indexer)
    except ImportError:
        cls = None
    if cls is None:
        log.warning(
            "Indexer plugin '%s' not found, falling back to default", config.indexer
        )
        return DefaultIndexer(config)
    return cls(config)


# Backwards-compat re-exports (indexer.py and search.py still exist for now)
from workspace.indexer import ensure_workspace_dirs, index_workspace  # noqa: E402, F401
from workspace.search import search_workspace  # noqa: E402, F401

__all__ = [
    "BaseIndexer",
    "DefaultIndexer",
    "WorkspaceConfig",
    "load_workspace_config",
    "get_indexer",
    "index_workspace",
    "search_workspace",
    "ensure_workspace_dirs",
    "IndexingError",
    "IndexSummary",
    "SearchResult",
]
