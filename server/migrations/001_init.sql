CREATE TABLE IF NOT EXISTS players (
    id UUID PRIMARY KEY,
    vrc_user_id VARCHAR(64) UNIQUE NOT NULL,
    current_vrc_username VARCHAR(128) NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_players_current_username_lower
ON players (LOWER(current_vrc_username));

CREATE TABLE IF NOT EXISTS player_names (
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    vrc_username VARCHAR(128) NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (player_id, vrc_username)
);

CREATE INDEX IF NOT EXISTS idx_player_names_username_lower
ON player_names (LOWER(vrc_username));

CREATE TABLE IF NOT EXISTS game_sessions (
    id UUID PRIMARY KEY,
    external_session_id VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('running', 'completed', 'abandoned')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_game_sessions_active_external_id
ON game_sessions (external_session_id)
WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_game_sessions_started_at
ON game_sessions (started_at DESC);

CREATE TABLE IF NOT EXISTS game_player_results (
    game_session_id UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    vrc_username VARCHAR(128) NOT NULL,
    world VARCHAR(128) NOT NULL,
    stage VARCHAR(128) NOT NULL,
    class_name VARCHAR(128) NOT NULL,
    boss_name VARCHAR(128) NOT NULL,
    boss_phase DOUBLE PRECISION,
    boss_damage BIGINT NOT NULL DEFAULT 0 CHECK (boss_damage >= 0),
    session_total_damage BIGINT NOT NULL DEFAULT 0 CHECK (session_total_damage >= 0),
    damage_taken BIGINT NOT NULL DEFAULT 0 CHECK (damage_taken >= 0),
    defeated_count INTEGER NOT NULL DEFAULT 0 CHECK (defeated_count >= 0),
    intermission BOOLEAN NOT NULL DEFAULT FALSE,
    last_sequence BIGINT NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    joined_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    left_at TIMESTAMPTZ,
    PRIMARY KEY (game_session_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_results_player_session
ON game_player_results (player_id, game_session_id);
