"""Pydantic contracts for model decisions and externally sourced project results.

Keep these schemas at the boundary between probabilistic model/API output and
application behavior. Do not rely on prompt wording alone for machine decisions.
"""
from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EngagementDecision(BaseModel):
    """Typed output contract for the spontaneous-engagement classifier."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    decision: Literal["JOIN", "IGNORE"] = Field(
        description="JOIN if Rem should reply to the latest conversation candidate; otherwise IGNORE."
    )


class ModrinthProjectResult(BaseModel):
    """Validated canonical Modrinth project record returned to Rem as tool data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    summary: str = ""
    project_type: Literal["mod", "modpack", "resourcepack", "datapack", "shader"]
    primary_project_type: str | None = None
    all_project_types: list[str] = Field(default_factory=list)
    url: str
    downloads: int | None = None
    followers: int | None = None
    latest_indexed_update: str | None = None
    client_side: Any = None
    server_side: Any = None
    environment: Any = None
    categories: list[str] = Field(default_factory=list)
    game_versions: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def require_canonical_modrinth_url(cls, value: str, info: Any) -> str:
        """Allow only a canonical HTTPS Modrinth project URL for its stated type."""
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").casefold()
        path = [part for part in parts.path.split("/") if part]
        if parts.scheme != "https" or host != "modrinth.com" or len(path) != 2:
            raise ValueError("url must be https://modrinth.com/<project_type>/<slug>")
        allowed_types = {"mod", "modpack", "resourcepack", "datapack", "shader"}
        if path[0] not in allowed_types or not path[1]:
            raise ValueError("url has an unsupported Modrinth project path")
        expected_type = info.data.get("project_type")
        if expected_type and path[0] != expected_type:
            raise ValueError("url project type must match project_type")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", path[1]):
            raise ValueError("url slug contains non-canonical characters")
        # Discard fragments and tracking queries; store a stable, canonical URL.
        return f"https://modrinth.com/{path[0]}/{path[1]}"


class ModrinthSearchOutput(BaseModel):
    """Consistent typed shape for both successful and failed Modrinth searches."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    query: str | None = None
    requested_project_type: str | None = None
    results: list[ModrinthProjectResult] = Field(default_factory=list)
    source: str | None = None
    error: str | None = None
