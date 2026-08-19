from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class EndpointKind(StrEnum):
    RSS = "rss"
    HORIZON_HACKER_NEWS = "horizon_hackernews"


@dataclass(frozen=True)
class SourceEndpoint:
    id: str
    source_id: str
    kind: EndpointKind
    locator: str
    policy_version: str
    checkpoint: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchedDocument:
    observed_at: datetime
    media_type: str
    payload: bytes
    source_url: str
    upstream_id: str | None = None
    role: str = "content"
