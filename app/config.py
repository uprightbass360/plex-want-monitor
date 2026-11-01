from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    port: int
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        return cls(
            data_dir=Path(env.get("WM_DATA_DIR", "data")),
            port=int(env.get("WM_PORT", "8787")),
            log_level=env.get("WM_LOG_LEVEL", "INFO").upper(),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "want.db"

    @property
    def plex_path(self) -> Path:
        return self.data_dir / "plex.json"
