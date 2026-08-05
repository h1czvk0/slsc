import os
from dataclasses import dataclass


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    sync_api_key: str
    persist_interval_seconds: float
    empty_room_ttl_seconds: float
    allowed_origins: tuple[str, ...]

    @classmethod
    def from_env(cls):
        origins = tuple(
            item.strip()
            for item in os.getenv("ALLOWED_ORIGINS", "*").split(",")
            if item.strip()
        )
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://slashco:slashco@127.0.0.1:5432/slashco",
            ),
            sync_api_key=os.getenv("SYNC_API_KEY", "").strip(),
            persist_interval_seconds=_positive_float("PERSIST_INTERVAL_SECONDS", 2.0),
            empty_room_ttl_seconds=_positive_float("EMPTY_ROOM_TTL_SECONDS", 300.0),
            allowed_origins=origins or ("*",),
        )
