"""Configuration and settings."""

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


class Settings(BaseModel):
    """Application settings."""

    model_config = ConfigDict(extra="forbid")

    data_dir: Path = Path("./data")
    http_mode: Literal["network", "replay", "record"] = "network"
    # Set together with http_mode by the CLI's --http-replay / --http-record options (plan 5.4);
    # make_http_client (http/client.py) is what actually requires one when http_mode needs it.
    http_replay_dir: Path | None = None
    http_record_dir: Path | None = None
    today: date | None = None

    @field_validator("data_dir", "http_replay_dir", "http_record_dir", mode="before")
    @classmethod
    def resolve_path(cls, v: str | Path | None) -> Path | None:
        """Convert string to Path."""
        if v is None:
            return None
        if isinstance(v, str):
            return Path(v).expanduser().resolve()
        return v.expanduser().resolve()
