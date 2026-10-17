"""Pure moderation policy helpers shared by runtime code and tests."""


def must_block_conversation_agent(classification: str | None) -> bool:
    """Fail closed when moderation cannot classify a guest server message.

    This never means punishment is appropriate. It only means the message
    must not bypass the required moderation stage and continue into Rem's
    conversational agent without a valid moderation result.
    """
    return str(classification or "UNAVAILABLE").strip().upper() == "UNAVAILABLE"
