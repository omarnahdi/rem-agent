"""Small, pure fail-closed gate for Rem's moderation pipeline.

Classification, abuse detection, target resolution, warnings, and timeouts live
in ``moderation.py``. This module only decides whether a message is allowed to
continue into Rem's conversational agent after the moderation stage.
"""

from __future__ import annotations

VALID_MODERATION_CLASSIFICATIONS = frozenset({"SAFE", "MILD_INSULT", "ABUSE"})


def must_block_conversation_agent(classification: str | None) -> bool:
    """Return True unless moderation produced a recognized classification.

    This is a fail-closed routing rule, not a punishment decision. Only the
    three labels emitted by the moderation classifier may proceed. Missing,
    unavailable, malformed, or unexpected labels block the conversational agent
    so an unmoderated guest message cannot bypass the mandatory moderation
    stage. The caller must not infer that blocking means the user should be
    punished.
    """
    normalized = str(classification or "").strip().upper()
    return normalized not in VALID_MODERATION_CLASSIFICATIONS
