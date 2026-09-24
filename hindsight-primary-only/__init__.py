"""Hindsight: retain only from primary (human-facing) sessions.

Hermes passes ``agent_context`` to ``MemoryProvider.initialize``: ``"primary"`` for
a human session, ``"cron"`` for a scheduled job, ``"subagent"`` for delegate_task.
The ABC says providers skip writes for non-primary contexts
(``agent/memory_provider.py``), and Honcho and Supermemory do. The bundled
Hindsight provider never reads it, so every cron run's turn was auto-retained, and
scheduled jobs ended up writing most of the bank.

This wraps the provider class so that:
- ``initialize`` records ``agent_context`` and ``platform`` on the instance;
- ``sync_turn`` returns early outside a primary context.

Explicit tool calls (``hindsight_retain``) are untouched, so a cron prompt that
deliberately asks for a retain still works. Recall is untouched too, so cron
jobs still read memory.

Why a plugin: plugins never touch core (``plugins/AGENTS.md``), and a patched
``plugins/memory/hindsight/__init__.py`` would be silently reverted by the next
``hermes update``. The upstream fix is the same check inside the provider.
"""

from __future__ import annotations

import functools
import importlib
import logging

logger = logging.getLogger(__name__)

_MARK = "_lobs_primary_only_wrapped"
_SKIP_CONTEXTS = frozenset({"cron", "subagent", "flush"})


def _is_non_primary(provider) -> bool:
    ctx = getattr(provider, "_lobs_agent_context", "") or ""
    platform = getattr(provider, "_lobs_platform", "") or ""
    return ctx in _SKIP_CONTEXTS or platform == "cron"


def _wrap(cls) -> bool:
    if getattr(cls, _MARK, False):
        return False
    orig_init = cls.initialize
    orig_sync = cls.sync_turn

    @functools.wraps(orig_init)
    def initialize(self, session_id, **kwargs):
        self._lobs_agent_context = str(kwargs.get("agent_context") or "")
        self._lobs_platform = str(kwargs.get("platform") or "")
        return orig_init(self, session_id, **kwargs)

    @functools.wraps(orig_sync)
    def sync_turn(self, user_content, assistant_content, **kwargs):
        if _is_non_primary(self):
            logger.debug("hindsight-primary-only: skipped auto-retain (agent_context=%s, platform=%s)",
                         getattr(self, "_lobs_agent_context", ""), getattr(self, "_lobs_platform", ""))
            return None
        return orig_sync(self, user_content, assistant_content, **kwargs)

    cls.initialize = initialize
    cls.sync_turn = sync_turn
    setattr(cls, _MARK, True)
    return True


def register(ctx) -> None:
    try:
        mod = importlib.import_module("plugins.memory.hindsight")
    except Exception as exc:  # hindsight not installed: nothing to guard
        logger.info("hindsight-primary-only: hindsight provider not importable (%s); inactive", exc)
        return
    cls = getattr(mod, "HindsightMemoryProvider", None)
    if cls is None:
        logger.warning("hindsight-primary-only: HindsightMemoryProvider not found; inactive")
        return
    if _wrap(cls):
        logger.info("hindsight-primary-only: auto-retain limited to primary sessions")
