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
    today: date | None = None

    @field_validator("data_dir", mode="before")
    @classmethod
    def resolve_path(cls, v: str | Path) -> Path:
        """Convert string to Path."""
        if isinstance(v, str):
            return Path(v).expanduser().resolve()
        return v.expanduser().resolve()
