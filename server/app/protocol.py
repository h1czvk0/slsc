from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


PROTOCOL_VERSION = 1
MAX_ROOM_PLAYERS = 4
MAX_MESSAGE_BYTES = 16 * 1024


class StrictMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlayerIdentity(StrictMessage):
    vrc_user_id: str = Field(min_length=5, max_length=64, pattern=r"^usr_[A-Za-z0-9-]+$")
    vrc_username: str = Field(min_length=1, max_length=128)

    @field_validator("vrc_username")
    @classmethod
    def clean_username(cls, value):
        cleaned = value.strip()
        if not cleaned or any(ord(character) < 32 for character in cleaned):
            raise ValueError("invalid VRC username")
        return cleaned


class JoinMessage(StrictMessage):
    type: Literal["join"]
    protocol_version: Literal[PROTOCOL_VERSION]
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    player: PlayerIdentity


class GameState(StrictMessage):
    world: str = Field(default="Ecliptica", min_length=1, max_length=128)
    stage: str = Field(default="-", min_length=1, max_length=128)
    class_name: str = Field(default="-", min_length=1, max_length=128)
    boss_name: str = Field(default="-", min_length=1, max_length=128)
    boss_phase: float | None = Field(default=None, ge=0, le=1000)
    boss_damage: int = Field(default=0, ge=0, le=9_223_372_036_854_775_807)
    session_total_damage: int = Field(default=0, ge=0, le=9_223_372_036_854_775_807)
    damage_taken: int = Field(default=0, ge=0, le=9_223_372_036_854_775_807)
    defeated_count: int = Field(default=0, ge=0, le=1_000_000)
    intermission: bool = False


class DamageUpdate(StrictMessage):
    type: Literal["damage_update"]
    protocol_version: Literal[PROTOCOL_VERSION]
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    sequence: int = Field(ge=1, le=9_223_372_036_854_775_807)
    client_timestamp_ms: int = Field(ge=0)
    player: PlayerIdentity
    game: GameState


class PingMessage(StrictMessage):
    type: Literal["ping"]
    protocol_version: Literal[PROTOCOL_VERSION]
    client_timestamp_ms: int = Field(ge=0)
