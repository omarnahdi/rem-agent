# ============================================================
# REM - STATELESS + IN-MEMORY MODERATION
# ============================================================

import asyncio
import os
import json
import re
from typing import Optional
from datetime import timedelta

import discord
from dotenv import load_dotenv

from .commands import timeout_deadline_matches
from .moderation_policy import must_block_conversation_agent

# Load the same .env before reading GROQ_API_KEY, independent of import order.
load_dotenv()

try:
    from groq import AsyncGroq
except ImportError:
    AsyncGroq = None


MODERATION_MODEL = "gemini-3.5-flash-lite"

# Groq is used ONLY as the moderation fallback when Gemini fails.
# Free-tier fallback model selected by design: GPT-OSS 20B.
GROQ_MODERATION_MODEL = "openai/gpt-oss-20b"


# ============================================================
# MODERATION CONFIGURATION
# ============================================================

# Known text references that can identify Master in server conversation.
# The Discord user ID remains authoritative; these aliases only identify
# when a message is referring to Master.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

groq = (
    AsyncGroq(api_key=GROQ_API_KEY)
    if AsyncGroq is not None and GROQ_API_KEY
    else None
)


MASTER_TEXT_ALIASES = (
    "nightxwolf",
    "nightx wolf",
    "nightx",
    "omar",
    "omar nahdi",
    "umar",
    "umar nahdi",
    "nalla",
    "nallaa",
    "berojgaar",
    "berojgar",
    "master",
)

MASTER_FAST_ABUSE_PATTERNS = (
    # User-provided abusive keyword variants. Keep word boundaries so these
    # do not match unrelated longer words; content is case-folded before use.
    r"\brand\b",
    r"\br\.a\.n\.d\b",

    # Clear Hinglish / Hindi-in-Latin-script abuse
    r"\bgandu\b",
    r"\bgaandu\b",
    r"\bchutiya\b",
    r"\bchutiye\b",
    r"\bmadarchod\b",
    r"\bmaderchod\b",
    r"\bbhenchod\b",
    r"\bbhosdi[a-z]*\b",
    r"\bbhosdike\b",
    r"\blavde\b",
    r"\bbsdk\b",
    r"\bmkc\b",

    # Dalla / dalle
    r"\bdalla\b",
    r"\bdalle\b",

    # Clear English profanity / insults
    r"\bfuck\s+(?:you|u)\b",
    r"\bfuck\s+off\b",
    r"\bpiece\s+of\s+shit\b",
    r"\basshole\b",
    r"\bdumbass\b",

    # Direct personal insults
    r"\b(?:is|are)\s+(?:a\s+)?(?:idiot|moron|stupid|dumb)\b",
    r"\b(?:you(?:'re|\s+are))\s+(?:a\s+)?(?:idiot|moron|stupid|dumb)\b",

    # Explicit "name + insult" forms
    r"\b(?:idiot|moron|stupid|dumb)\s+"
    r"(?:master|omar|omar\s+nahdi|umar|umar\s+nahdi|nalla|nallaa|berojgaar|berojgar|nightx|nightxwolf|nightx\s+wolf)\b",
)

print(f"[MODERATION MODULE] Loaded from: {__file__}")


# ============================================================
# IN-MEMORY ABUSE TRACKING
# ============================================================

# Key:
# (guild_id, offender_id)
#
# Value:
# Number of genuine abusive messages detected
#
# This exists only while the bot is running.
ABUSE_COUNTS = {}


def get_abuse_count(
    guild_id: int,
    offender_id: int
) -> int:

    return ABUSE_COUNTS.get(
        (guild_id, offender_id),
        0
    )


def increment_abuse_count(
    guild_id: int,
    offender_id: int
) -> int:

    key = (
        guild_id,
        offender_id
    )

    ABUSE_COUNTS[key] = (
        ABUSE_COUNTS.get(key, 0) + 1
    )

    return ABUSE_COUNTS[key]


def reset_abuse_count(
    guild_id: int,
    offender_id: int
):

    ABUSE_COUNTS.pop(
        (guild_id, offender_id),
        None
    )


# ============================================================
# MODERATION CONTEXT + TARGET DETECTION
# ============================================================

# Moderation is intentionally broader than Rem's conversational context.
# Every non-Master guild message is inspected, and this context helps the
# classifier understand short follow-ups such as "lavde", "haan bhai", etc.
MODERATION_CONTEXT_MESSAGE_LIMIT = 12
MODERATION_CONTEXT_CHAR_LIMIT = 6000
TARGET_ALIAS_STOPWORDS = {
    "the",
    "this",
    "that",
    "with",
    "from",
    "your",
    "you",
    "fuck",
    "off",
    "tell",
    "give",
    "please",
    "master",
    "rem",
}


def _member_text_aliases(
    member: discord.Member,
) -> set[str]:
    """Build conservative, whole-word text aliases for a guild member."""
    names = {
        str(getattr(member, "display_name", "") or "").strip().casefold(),
        str(getattr(member, "name", "") or "").strip().casefold(),
    }

    aliases: set[str] = set()

    for name in names:
        if not name:
            continue

        aliases.add(name)

        for token in re.findall(r"[a-z]{3,}", name):
            if token not in TARGET_ALIAS_STOPWORDS:
                aliases.add(token)

    return aliases


def _text_mentions_member(
    content: str,
    member: discord.Member,
) -> bool:
    """Match a member alias as a whole word, never as a substring."""
    content = (content or "").casefold()

    return any(
        re.search(
            rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
            content,
        )
        for alias in _member_text_aliases(member)
    )


def _member_identity(member: discord.Member) -> str:
    """Return stable identity metadata for moderation logs/prompts."""
    return (
        f"display_name='{member.display_name}' | "
        f"username=@{member.name} | "
        f"mention={member.mention} | "
        f"user_id={member.id}"
    )


def _message_identity_line(message: discord.Message) -> str:
    """Return one structured message line with author/reply metadata."""
    author = message.author
    username = getattr(author, "name", None) or getattr(author, "display_name", "unknown")
    mention = getattr(author, "mention", f"<@{author.id}>")
    reply_id = (
        message.reference.message_id
        if message.reference and message.reference.message_id
        else None
    )

    return (
        f"[MESSAGE id={message.id} | "
        f"author: display_name='{author.display_name}' | "
        f"username=@{username} | "
        f"mention={mention} | "
        f"user_id={author.id} | "
        f"reply_to_message_id={reply_id}]"
    )


def _message_mentions_line(message: discord.Message) -> str:
    """Return all explicit member mentions with full identity metadata."""
    mentions = []

    for member in getattr(message, "mentions", []) or []:
        mentions.append(
            _member_identity(member)
        )

    return (
        "MENTIONS: " + "; ".join(mentions)
        if mentions
        else "MENTIONS: None"
    )


async def build_moderation_context(
    message: discord.Message,
) -> list[discord.Message]:
    """Load recent shared messages for moderation context."""
    if not message.guild:
        return []

    messages: list[discord.Message] = []

    try:
        async for previous in message.channel.history(
            limit=MODERATION_CONTEXT_MESSAGE_LIMIT,
            before=message,
        ):
            messages.append(previous)
    except (
        discord.Forbidden,
        discord.HTTPException,
    ) as e:
        print(
            f"[MODERATION CONTEXT] History fetch failed: {type(e).__name__}: {e}"
        )
        return []

    messages.reverse()

    print(
        f"[MODERATION CONTEXT] Loaded {len(messages)} "
        f"recent message(s) for message {message.id}."
    )

    return messages


def format_moderation_context(
    messages: list[discord.Message],
) -> str:
    """Format recent moderation context with authoritative user identities."""
    if not messages:
        return "No recent shared conversation available."

    blocks: list[str] = []
    total_chars = 0

    for previous in messages:
        content = previous.content.strip()
        if not content and previous.attachments:
            content = "[attachment]"
        elif not content:
            continue

        block = (
            f"{_message_identity_line(previous)}\n"
            f"{_message_mentions_line(previous)}\n"
            f"CONTENT: {content}"
        )

        if total_chars + len(block) + 1 > MODERATION_CONTEXT_CHAR_LIMIT:
            remaining = MODERATION_CONTEXT_CHAR_LIMIT - total_chars
            if remaining > 120:
                block = block[:remaining - 4] + "..."
                blocks.append(block)
            break

        blocks.append(block)
        total_chars += len(block) + 1

    return "\n\n".join(blocks) if blocks else "No recent shared conversation available."


async def _direct_target_member(
    message: discord.Message,
    master_id: int,
) -> Optional[discord.Member]:
    """Resolve only targets explicitly indicated by the current message."""
    # 1. Explicit Discord mention
    for member in message.mentions:
        if not member.bot:
            return member

    # 2. Reply target
    if message.reference and message.reference.message_id and message.guild:
        try:
            referenced_message = getattr(message.reference, "resolved", None)
            if not isinstance(referenced_message, discord.Message):
                referenced_message = await message.channel.fetch_message(
                    message.reference.message_id
                )

            target = referenced_message.author
            if not target.bot:
                return target
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            pass

    if not message.guild:
        return None

    content = message.content.casefold()

    # 3. Known Master aliases
    for alias in MASTER_TEXT_ALIASES:
        if re.search(
            rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
            content,
        ):
            master = message.guild.get_member(master_id)
            if master is None:
                try:
                    master = await message.guild.fetch_member(master_id)
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    master = None

            if master is not None and not master.bot:
                return master

    # 4. Unique written member name / alias
    candidates: list[discord.Member] = []

    for member in message.guild.members:
        if member.bot or member.id == message.author.id:
            continue
        if _text_mentions_member(content, member):
            candidates.append(member)

    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1:
        print(
            "[MODERATION TARGET TEXT] Ambiguous current-message target -> "
            + ", ".join(member.display_name for member in candidates[:8])
        )

    return None


async def get_target_member(
    message: discord.Message,
    master_id: int,
    recent_messages: list[discord.Message] | None = None,
) -> Optional[discord.Member]:
    """
    Resolve a moderation target without making target detection a moderation gate.

    Priority:
    1. Explicit Discord mention
    2. Reply target
    3. Explicit Master alias / written member alias
    4. Most recent unambiguous target of the same author in recent context

    The contextual step exists for short follow-ups such as "lavde" where the
    previous message clearly established who the speaker was addressing.
    """
    target = await _direct_target_member(message, master_id)
    if target is not None:
        return target

    # Contextual continuation: look for the most recent earlier message from
    # this same author that had an unambiguous direct target. This is deliberately
    # shallow and only used to resolve a missing target; moderation still runs
    # even when no target can be established.
    for previous in reversed(recent_messages or []):
        if previous.author.id != message.author.id:
            continue

        contextual_target = await _direct_target_member(
            previous,
            master_id,
        )

        if contextual_target is not None:
            print(
                "[MODERATION TARGET CONTEXT] "
                f"Inherited from message {previous.id} -> "
                f"{_member_identity(contextual_target)}"
            )
            return contextual_target

        # Only inspect the nearest previous message by the same author.
        break

    return None


async def infer_contextual_target_after_abuse(
    message: discord.Message,
    recent_messages: list[discord.Message] | None,
    master_id: int,
) -> Optional[discord.Member]:
    """
    Infer the person an abusive message is directed at from the immediate
    conversation when Python could not resolve an explicit target.

    This is intentionally called only AFTER the classifier has determined
    that the current message is ABUSE. It does not make target inference a
    prerequisite for moderation and therefore preserves the rule that every
    non-Master human guild message is classified.

    The nearest previous human message is treated as the conversational
    target. Bot messages are ignored because Rem's own warning/reply should
    not become the target of a following abusive message.
    """
    if not message.guild:
        return None

    for previous in reversed(recent_messages or []):
        author = previous.author

        if getattr(author, "bot", False):
            continue

        if author.id == message.author.id:
            continue

        if author.id == master_id:
            print(
                "[MODERATION TARGET CONTEXT] "
                "ABUSE message follows Master's message -> "
                f"{_member_identity(author)}"
            )
            return author if isinstance(author, discord.Member) else message.guild.get_member(master_id)

        # Another human may be the conversational target. Resolve that member
        # from the guild cache to keep the target identity stable.
        member = message.guild.get_member(author.id)
        if member is not None and not member.bot:
            print(
                "[MODERATION TARGET CONTEXT] "
                "ABUSE message follows another human's message -> "
                f"{_member_identity(member)}"
            )
            return member

        break

    return None


async def fast_master_abuse_check(
    message: discord.Message,
    target: Optional[discord.Member],
    master_id: int,
) -> dict | None:
    """Detect obvious English/Hinglish abuse when the resolved target is Master."""
    if target is None or target.id != master_id:
        return None

    content = message.content.casefold()

    for pattern in MASTER_FAST_ABUSE_PATTERNS:
        if re.search(pattern, content):
            return {
                "classification": "ABUSE",
                "confidence": 0.99,
                "reason": "Clear English/Hinglish abuse directed at Master.",
            }

    return None


# ============================================================
# GEMINI MODERATION CLASSIFIER
# ============================================================

async def _parse_moderation_response(
    raw: str,
) -> dict:
    """
    Parse a moderation model response into the stable moderation schema.
    """

    raw = (raw or "").strip()

    # Remove accidental markdown fences.
    raw = raw.replace(
        "```json",
        ""
    )

    raw = raw.replace(
        "```",
        ""
    )

    raw = raw.strip()

    # Prefer the full response as JSON. If a model surrounded the JSON
    # with stray text, recover the first JSON object conservatively.
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(
            r"\{.*\}",
            raw,
            flags=re.DOTALL,
        )

        if not match:
            raise

        result = json.loads(
            match.group(0)
        )

    classification = str(
        result.get(
            "classification",
            "UNAVAILABLE"
        )
    ).upper()

    confidence = float(
        result.get(
            "confidence",
            0.0
        )
    )

    reason = str(
        result.get(
            "reason",
            ""
        )
    )

    if classification not in {
        "SAFE",
        "MILD_INSULT",
        "ABUSE",
    }:
        classification = "UNAVAILABLE"

    return {
        "classification": classification,
        "confidence": max(
            0.0,
            min(
                1.0,
                confidence
            )
        ),
        "reason": reason,
    }


async def _groq_moderation_fallback(
    prompt: str,
) -> dict:
    """
    Run the moderation classifier through Groq GPT-OSS 20B.

    This is strictly a fallback for Gemini failures. It does not change
    moderation policy or execute any moderation action itself.
    """

    if groq is None:
        print(
            "[MODERATION GROQ] "
            "Fallback unavailable. "
            "Set GROQ_API_KEY and install the groq package."
        )
        raise RuntimeError(
            "Groq moderation fallback is unavailable."
        )

    fallback_prompt = f"""
{prompt}

FALLBACK EXECUTION RULES:
- You are the same moderation classifier operating in fallback mode.
- Return ONLY valid JSON.
- Use exactly the same three classifications: SAFE, MILD_INSULT, ABUSE.
- confidence must be a number from 0.0 to 1.0.
- Do not add markdown or extra fields.
"""

    try:
        print(
            f"[MODERATION GROQ] "
            f"Gemini unavailable. Trying {GROQ_MODERATION_MODEL}..."
        )

        response = await groq.chat.completions.create(
            model=GROQ_MODERATION_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Rem's moderation classifier. "
                        "Follow the supplied moderation prompt exactly."
                    ),
                },
                {
                    "role": "user",
                    "content": fallback_prompt,
                },
            ],
            temperature=0.0,
            max_completion_tokens=300,
            include_reasoning=False,
            stream=False,
        )

        raw = (
            response.choices[0].message.content
            if response.choices
            else ""
        )

        result = await _parse_moderation_response(raw)
        if result["classification"] == "UNAVAILABLE":
            raise ValueError("Groq returned an invalid moderation classification.")

        print(
            "[MODERATION GROQ] "
            f"{GROQ_MODERATION_MODEL} responded successfully."
        )

        return result

    except Exception as e:
        print(
            "[MODERATION GROQ] "
            f"Fallback error: {e}"
        )
        raise


async def classify_message(
    gemini,
    message: discord.Message,
    target: Optional[discord.Member],
    moderation_context: str = "",
) -> dict:
    """Classify every non-Master guild message, with context when available."""

    author = message.author
    target_block = (
        _member_identity(target)
        if target is not None
        else "No unambiguous direct target was resolved by Python."
    )

    prompt = f"""
You are Rem's moderation classifier.

Your job is ONLY to analyze the CURRENT Discord message and classify it.
Every message sent to you must be classified, even when no explicit target is
present. Do not skip a message merely because the current message is short,
slang, a standalone profanity, or lacks a Discord mention.

Do NOT decide Discord permissions.
Do NOT execute moderation.
Do NOT invent previous behavior.
Do NOT infer the current speaker from names in context; the current speaker's
identity below is authoritative.

CURRENT AUTHOR:
{_member_identity(author)}

CURRENT RESOLVED TARGET (Python):
{target_block}

CURRENT MESSAGE:
{message.content}

RECENT SHARED DISCORD CONTEXT:
{moderation_context or "No recent shared conversation available."}

CONTEXT RULES:
- Use the recent context to understand short follow-ups and conversational
  continuation, especially messages like "lavde", "haan", "haan bhai", "right?",
  "usne kya bola?", or similar Hinglish/Discord shorthand.
- User IDs, mentions, and author metadata are authoritative for identifying
  who said what. Do not merge users because their display names look similar.
- If the current message continues the immediately preceding speaker's directed
  exchange, use that context when interpreting the message.
- Do not invent a target when the context does not support one.

Understand:
- English
- Hindi
- Hinglish
- Hindi written using English letters
- Natural English/Hinglish code-switching
- Common Discord slang and abbreviations
- Profanity
- Insults
- Harassment
- Friendly banter
- Context-dependent continuation

Language rule:
Interpret the message semantically in English/Hindi/Hinglish.
Do not downgrade clearly abusive meaning because of Hindi/Hinglish spelling,
grammar, transliteration, or mixed-language wording.

Important rules:

1. Normal joking or friendly banter is NOT automatically abuse.

2. A single mild insult may be MILD_INSULT when it is not genuine harassment
   or aggressive personal abuse.

3. Genuine profanity directed at another person is ABUSE.

4. Strong personal insults, degrading language, harassment, or aggressive
   profanity should be ABUSE.

5. A short standalone profanity can still be ABUSE when the surrounding
   conversation clearly shows it is directed at a person.

6. Context matters. Do not classify every casual slang word as abuse.

7. Hindi/Hinglish profanity must be understood even when written using
   English characters.

8. If the message is clearly playful banter between friends, prefer SAFE or
   MILD_INSULT rather than ABUSE.

Return ONLY valid JSON.

Use exactly this structure:

{{
    "classification": "SAFE",
    "confidence": 0.0,
    "reason": "short explanation"
}}

classification MUST be exactly one of:
SAFE
MILD_INSULT
ABUSE

confidence must be a number from 0.0 to 1.0.

Do not include markdown.
Do not include additional fields.
"""

    try:
        response = await asyncio.to_thread(
            gemini.models.generate_content,
            model=MODERATION_MODEL,
            contents=prompt,
        )

        result = await _parse_moderation_response(response.text)
        if result["classification"] == "UNAVAILABLE":
            raise ValueError("Gemini returned an invalid moderation classification.")
        return result

    except Exception as e:
        print(
            f"[MODERATION AI] Gemini classifier error: {type(e).__name__}: {e}"
        )

        try:
            return await _groq_moderation_fallback(prompt)
        except Exception as fallback_error:
            print(
                "[MODERATION AI] "
                f"All classifier paths failed: {type(fallback_error).__name__}: "
                f"{fallback_error}"
            )
            return {
                "classification": "UNAVAILABLE",
                "confidence": 0.0,
                "reason": "Moderation classifiers unavailable; no classification was made.",
            }


# ============================================================
# MODERATION ACTION
# ============================================================

async def moderate_message(
    gemini,
    message: discord.Message,
    master_id: int,
    bot_user_id: int,
) -> bool:
    """
    Run AutoMod for EVERY non-Master human guild message.

    The caller continues to exclude Master, preserving Master's immunity.

    Guest -> Guest:
        1st abuse = warning
        2nd abuse = 1-minute timeout

    Guest -> Master:
        genuine abuse = immediate 1-minute timeout

    Target detection is NOT a prerequisite for classification anymore.
    """

    # --------------------------------------------------------
    # Basic safety / Master immunity stays exactly here.
    # --------------------------------------------------------

    if not message.guild:
        return False

    if message.author.id == bot_user_id:
        return False

    # Master remains completely immune to AutoMod punitive handling.
    if message.author.id == master_id:
        return False

    author = message.author

    print(
        "[MODERATION CHECK] "
        f"Author={author.display_name} | "
        f"Username=@{author.name} | "
        f"Mention={author.mention} | "
        f"UserID={author.id} | "
        f"MessageID={message.id} | "
        f"Content={message.content!r}"
    )

    # --------------------------------------------------------
    # Build moderation context for EVERY message.
    # --------------------------------------------------------

    recent_messages = await build_moderation_context(message)
    moderation_context = format_moderation_context(recent_messages)

    print(
        f"[MODERATION CONTEXT] chars={len(moderation_context)} | "
        f"messages={len(recent_messages)}"
    )

    # --------------------------------------------------------
    # Resolve a target if one exists, but NEVER use target absence
    # as a reason to skip classification.
    # --------------------------------------------------------

    target = await get_target_member(
        message,
        master_id,
        recent_messages,
    )

    if target is not None:
        if target.bot:
            print(
                "[MODERATION TARGET] Bot target detected -> no punitive target action."
            )
        elif target.id == author.id:
            print(
                "[MODERATION TARGET] Self-directed message -> no punitive target action."
            )
        else:
            print(
                "[MODERATION TARGET] "
                f"{_member_identity(target)}"
            )
    else:
        print(
            "[MODERATION TARGET] Unresolved; message will STILL be classified."
        )

    # Don't use bot/self targets as actionable targets, but retain the fact
    # that the classifier saw the message.
    action_target = target
    if action_target is not None and (
        action_target.bot or action_target.id == author.id
    ):
        action_target = None

    # --------------------------------------------------------
    # Fast-path obvious Master abuse.
    # --------------------------------------------------------

    fast_result = await fast_master_abuse_check(
        message,
        action_target,
        master_id,
    )

    if fast_result is not None:
        result = fast_result
        print(
            "[MODERATION FAST] "
            f"Classification={result['classification']} | "
            f"Confidence={result['confidence']:.2f} | "
            f"Target=Master | "
            f"Reason={result['reason']}"
        )
    else:
        # ----------------------------------------------------
        # EVERY message reaches the AI classifier when the fast
        # path does not deterministically handle it.
        # ----------------------------------------------------
        result = await classify_message(
            gemini,
            message,
            action_target,
            moderation_context,
        )

    classification = result["classification"]
    confidence = result["confidence"]

    if must_block_conversation_agent(classification):
        # Moderation is mandatory for non-Master guild messages. Do not punish
        # an author when neither classifier can produce a valid result, but do
        # fail closed: the caller must not pass an unmoderated message to Rem's
        # conversational agent. Returning True means the message was handled
        # by this policy gate, not that a punitive action was taken.
        print(
            "[MODERATION RESULT] UNAVAILABLE -> fail closed; no punishment or "
            "counter update, and the message will not enter the conversation agent."
        )
        return True

    # Target resolution is normally performed before classification. If the
    # message was abusive but had no explicit/reply/name target, use the
    # immediate conversation to resolve who the abuse is directed at. This
    # is what lets short messages such as "lavde" or "fuck u" after a Master's
    # message receive the Guest -> Master immediate-timeout policy.
    if action_target is None and classification == "ABUSE":
        contextual_target = await infer_contextual_target_after_abuse(
            message,
            recent_messages,
            master_id,
        )

        if contextual_target is not None and contextual_target.id != author.id:
            action_target = contextual_target
            print(
                "[MODERATION TARGET CONTEXT] "
                "Resolved target after ABUSE classification: "
                f"{_member_identity(action_target)}"
            )

    target_label = (
        action_target.display_name
        if action_target is not None
        else "UNRESOLVED"
    )

    print(
        "[MODERATION AI] "
        f"Author={author.display_name} | "
        f"AuthorID={author.id} | "
        f"Target={target_label} | "
        f"Classification={classification} | "
        f"Confidence={confidence:.2f} | "
        f"Reason={result['reason']}"
    )

    # --------------------------------------------------------
    # SAFE
    # --------------------------------------------------------

    if classification == "SAFE":
        print("[MODERATION RESULT] SAFE -> no action.")
        return False

    # --------------------------------------------------------
    # MILD INSULT
    # --------------------------------------------------------

    if classification == "MILD_INSULT":
        print("[MODERATION RESULT] MILD_INSULT -> no punitive action.")
        return False

    # --------------------------------------------------------
    # Only ABUSE continues.
    # --------------------------------------------------------

    if classification != "ABUSE":
        print(
            f"[MODERATION RESULT] Unknown classification={classification!r} -> no action."
        )
        return False

    # --------------------------------------------------------
    # Require reasonable confidence.
    # --------------------------------------------------------

    if confidence < 0.70:
        print(
            "[MODERATION RESULT] ABUSE confidence below 0.70 -> no action."
        )
        return False

    # ========================================================
    # GUEST -> MASTER
    # ========================================================

    if action_target is not None and action_target.id == master_id:
        member = author
        me = message.guild.me

        if me is None:
            print("[AUTO-TIMEOUT] Guild member cache returned no bot member.")
            return False

        if not me.guild_permissions.moderate_members:
            print(
                "[AUTO-TIMEOUT] Rem lacks Timeout Members permission."
            )

            await message.reply(
                f"{member.mention}, please mind your words when speaking to Master. 🌸"
            )
            return True

        if member.top_role >= me.top_role:
            print(
                "[AUTO-TIMEOUT] Target role is too high."
            )

            await message.reply(
                f"{member.mention}, please keep it respectful when speaking to Master. 🌸"
            )
            return True

        try:
            expected_until = datetime.now(timezone.utc) + timedelta(minutes=1)
            await member.timeout(
                timedelta(minutes=1),
                reason="Automatic protection of Master",
            )
            verified_member = await message.guild.fetch_member(member.id)
            until = getattr(verified_member, "communication_disabled_until", None)
            if not timeout_deadline_matches(expected_until, until):
                print(
                    "[AUTO-TIMEOUT UNVERIFIED] Discord did not confirm the "
                    f"requested deadline for member_id={member.id}; expected="
                    f"{expected_until.isoformat()} actual="
                    f"{until.isoformat() if until else None}"
                )
                await message.reply(
                    f"{member.mention}, please keep it respectful toward Master. 🌸 "
                    "I couldn't verify the requested timeout, so I won't claim it was applied."
                )
                return True

            print(
                "[AUTO-TIMEOUT SUCCESS] "
                f"TargetAuthor={_member_identity(member)} | "
                "Duration=1 minute | "
                f"VerifiedUntil={until.isoformat()} | "
                "Reason=Automatic protection of Master"
            )

            await message.reply(
                f"{member.mention}, that's enough. 🌸 "
                f"I've verified your 1-minute timeout. "
                f"Please keep it respectful toward Master."
            )

            return True

        except discord.Forbidden:
            print(
                "[AUTO-TIMEOUT ERROR] Discord permission denied."
            )

            await message.reply(
                f"{member.mention}, please keep it respectful when speaking to Master. 🌸"
            )
            return True

        except discord.HTTPException as e:
            print(
                f"[AUTO-TIMEOUT ERROR] Discord HTTP error: {type(e).__name__}: {e}"
            )
            return False

        except Exception as e:
            print(
                f"[AUTO-TIMEOUT ERROR] Unexpected error: {type(e).__name__}: {e}"
            )
            return False

    # ========================================================
    # GUEST -> GUEST / GENERAL ABUSE
    # ========================================================

    guild_id = message.guild.id
    offender_id = author.id

    abuse_count = increment_abuse_count(
        guild_id,
        offender_id,
    )

    print(
        "[MODERATION COUNT] "
        f"Author={author.display_name} | "
        f"AuthorID={author.id} | "
        f"Count={abuse_count} | "
        f"Target={target_label}"
    )

    # --------------------------------------------------------
    # FIRST ABUSE -> WARNING
    # --------------------------------------------------------

    if abuse_count == 1:
        if action_target is not None:
            response = (
                f"{author.mention}, that's enough. 🌸 "
                f"Please keep it respectful toward {action_target.mention}. "
                f"I don't want to have to timeout you."
            )
        else:
            response = (
                f"{author.mention}, that's enough. 🌸 "
                f"Please keep it respectful. I don't want to have to timeout you."
            )

        await message.reply(response)

        print(
            "[MODERATION WARNING] "
            f"Author={author.display_name} | Count=1"
        )

        return True

    # --------------------------------------------------------
    # SECOND ABUSE -> TIMEOUT
    # --------------------------------------------------------

    member = author
    me = message.guild.me

    if me is None:
        print("[AUTO-TIMEOUT ERROR] Guild member cache returned no bot member.")
        return False

    if not me.guild_permissions.moderate_members:
        print(
            "[AUTO-TIMEOUT] Rem lacks Timeout Members permission."
        )

        await message.reply(
            f"{member.mention}, you've already been warned. 🌸 Please stop."
        )
        return True

    if member.top_role >= me.top_role:
        print(
            "[AUTO-TIMEOUT] Offender role is too high."
        )

        await message.reply(
            f"{member.mention}, you've already been warned. 🌸 Please stop."
        )
        return True

    try:
        expected_until = datetime.now(timezone.utc) + timedelta(minutes=1)
        await member.timeout(
            timedelta(minutes=1),
            reason="Second abusive message detected by Rem",
        )
        verified_member = await message.guild.fetch_member(member.id)
        until = getattr(verified_member, "communication_disabled_until", None)
        if not timeout_deadline_matches(expected_until, until):
            print(
                "[AUTO-TIMEOUT UNVERIFIED] Discord did not confirm the "
                f"requested deadline for member_id={member.id}; expected="
                f"{expected_until.isoformat()} actual="
                f"{until.isoformat() if until else None}"
            )
            await message.reply(
                f"{member.mention}, you've already been warned. 🌸 "
                "I couldn't verify the requested timeout, so I won't claim it was applied. Please stop."
            )
            return True

        print(
            "[AUTO-TIMEOUT SUCCESS] "
            f"TargetAuthor={_member_identity(member)} | "
            f"Duration=1 minute | "
            f"AbuseCount={abuse_count} | "
            f"VerifiedUntil={until.isoformat()} | "
            "Reason=Second abusive message detected by Rem"
        )

        reset_abuse_count(
            guild_id,
            offender_id,
        )

        if action_target is not None:
            response = (
                f"{member.mention}, you've already been warned. 🌸 "
                f"I've timed you out for 1 minute. "
                f"Please keep it respectful toward {action_target.mention}."
            )
        else:
            response = (
                f"{member.mention}, you've already been warned. 🌸 "
                f"I've timed you out for 1 minute. Please keep it respectful."
            )

        await message.reply(response)
        return True

    except discord.Forbidden:
        print(
            "[AUTO-TIMEOUT ERROR] Discord permission denied."
        )

        await message.reply(
            f"{member.mention}, you've already been warned. 🌸 Please stop."
        )
        return True

    except discord.HTTPException as e:
        print(
            f"[AUTO-TIMEOUT ERROR] Discord HTTP error: {type(e).__name__}: {e}"
        )
        return False

    except Exception as e:
        print(
            f"[AUTO-TIMEOUT ERROR] Unexpected error: {type(e).__name__}: {e}"
        )
        return False

