"""Authoritative Discord identity. Display names and claims never grant Master."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .constants import DEFAULT_MASTER_DISCORD_ID

MASTER_DISCORD_ID = DEFAULT_MASTER_DISCORD_ID

Role = Literal["MASTER", "GUEST"]


@dataclass(frozen=True, slots=True)
class SpeakerIdentity:
    discord_user_id: int
    display_name: str
    username: str
    role: Role

    @property
    def is_master(self) -> bool:
        return self.role == "MASTER"


def identify_speaker(
    *,
    discord_user_id: int,
    display_name: str,
    username: str,
    master_discord_id: int,
) -> SpeakerIdentity:
    """Only exact numeric user-ID equality grants MASTER status."""
    role: Role = "MASTER" if int(discord_user_id) == int(master_discord_id) else "GUEST"
    return SpeakerIdentity(
        discord_user_id=int(discord_user_id),
        display_name=display_name or username or str(discord_user_id),
        username=username or str(discord_user_id),
        role=role,
    )


def build_trusted_dependencies(
    identity: SpeakerIdentity,
    *,
    guild_id: int | None,
    channel_id: int,
) -> dict[str, str]:
    """Build per-run context without mutating shared Agent configuration."""
    return {
        "trusted_discord_user_id": str(identity.discord_user_id),
        "trusted_discord_display_name": identity.display_name,
        "trusted_discord_username": identity.username,
        "trusted_discord_role": identity.role,
        "trusted_discord_is_master": "true" if identity.is_master else "false",
        "trusted_discord_guild_id": str(guild_id) if guild_id is not None else "DM",
        "trusted_discord_channel_id": str(channel_id),
    }


def session_id_for(*, guild_id: int | None, channel_id: int, user_id: int) -> str:
    """Keep each speaker's per-channel history isolated during phase 1."""
    scope = f"guild-{guild_id}" if guild_id is not None else "dm"
    return f"discord:{scope}:channel-{channel_id}:user-{user_id}"
