import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecliptica_log_parser import (  # noqa: E402
    EclipticaEvent,
    EclipticaState,
    clean_stage_name,
    is_ecliptica_room,
    line_might_affect_ecliptica_state,
    parse_ecliptica_line,
    split_boss_name,
)


class EclipticaParserTests(unittest.TestCase):
    def test_room_and_stage_are_parsed_from_real_log_shapes(self):
        room = parse_ecliptica_line(
            "2026.07.26 01:44:01 Debug - [Behaviour] Entering Room: Ecliptica - Demo Playtest"
        )
        self.assertEqual(room.kind, "room_entered")
        self.assertTrue(is_ecliptica_room(room.groups[0]))

        stage = parse_ecliptica_line(
            "2026.07.25 18:50:01 Debug - ECLIPTICA - now in stage: "
            "Stage_BalboaRuins on phase: 0.9238784 as class: Thaumaturge"
        )
        self.assertEqual(stage.kind, "stage")
        self.assertEqual(stage.groups, ("Stage_BalboaRuins", "0.9238784", "Thaumaturge"))
        self.assertEqual(clean_stage_name(stage.groups[0]), "BalboaRuins")

    def test_boss_phase_suffix_is_normalized(self):
        name, key, phase = split_boss_name("DespairPhase2(Clone)")
        self.assertEqual(name, "Despair")
        self.assertEqual(key, "despair")
        self.assertEqual(phase, 2)

    def test_all_runtime_lines_are_selected_by_tail_filter(self):
        lines = (
            "ECLIPTICA saving SESSION ID 1005",
            "ECLIPTICA - now fighting boss: M41D(Clone) on phase: 0.5",
            "Boss M41D dead, personal damage dealt:",
            "STRIKE DMG: 14515",
            "NON-STRIKE DMG: 1270",
            "Dealing 204 STRIKE damage",
            "damage has been taken: 20, from source: (M-41-D) attack_DashKick",
        )
        self.assertTrue(all(line_might_affect_ecliptica_state(line) for line in lines))
        self.assertEqual(parse_ecliptica_line("NON-STRIKE DMG: 1270").kind, "non_strike_damage")
        self.assertEqual(parse_ecliptica_line("Dealing 204 STRIKE damage").groups, ("204", "STRIKE"))

    def test_parses_authentication_and_boss_ownership(self):
        authenticated = parse_ecliptica_line(
            "2026.07.26 16:04:50 Debug - User Authenticated: TestPlayer "
            "(usr_00000000-0000-0000-0000-000000000001)"
        )
        ownership = parse_ecliptica_line(
            "2026.07.26 16:22:02 Debug - ownership of Obisidus transferred to BangYaSan"
        )

        self.assertEqual(authenticated.kind, "authenticated")
        self.assertEqual(authenticated.groups[0], "TestPlayer")
        self.assertEqual(ownership.kind, "ownership")
        self.assertEqual(ownership.groups, ("Obisidus", "BangYaSan"))

    def test_boss_ownership_preserves_unicode_player_name(self):
        ownership = parse_ecliptica_line(
            "2026.07.26 16:22:02 Debug - ownership of Obisidus transferred to ಣಪರೀಕ್ಷೆ"
        )

        self.assertEqual(ownership.kind, "ownership")
        self.assertEqual(ownership.groups, ("Obisidus", "ಣಪರೀಕ್ಷೆ"))


class EclipticaStateTests(unittest.TestCase):
    def make_event(self, kind, *groups, timestamp=0.0):
        return EclipticaEvent(kind, tuple(str(group) for group in groups), timestamp)

    def test_damage_settlement_accumulates_boss_and_session_totals(self):
        state = EclipticaState()
        state.apply(self.make_event("stage", "Stage_VRCHub", "0.5", "Thaumaturge", timestamp=100.0))
        state.apply(self.make_event("boss", "Despair(Clone)", "0.5", timestamp=110.0))
        state.apply(self.make_event("boss_dead", "Despair", timestamp=120.0))
        state.apply(self.make_event("strike_damage", "5000", timestamp=120.0))
        state.apply(self.make_event("non_strike_damage", "500", timestamp=120.0))

        snapshot = state.snapshot(now=120.0)
        self.assertEqual(snapshot["current_boss"], "Despair")
        self.assertEqual(snapshot["current_boss_damage"], 5500)
        self.assertEqual(snapshot["session_total_damage"], 5500)
        self.assertEqual(snapshot["last_settlement_dps"], 550.0)
        self.assertEqual(state.settlements[0]["phase"], 1)
        self.assertEqual(state.settlements[0]["duration"], 10.0)

    def test_current_phase_tracks_live_damage_taken_dps_and_elapsed_time(self):
        state = EclipticaState()
        state.apply(self.make_event("boss", "JimBringerPhase2(Clone)", "0.5", timestamp=100.0))
        state.apply(self.make_event("damage_dealt", "200", "STRIKE", timestamp=101.0))
        state.apply(self.make_event("damage_dealt", "300", "STRIKE", timestamp=104.0))
        state.apply(self.make_event("damage_taken", "75", "attack_chop", timestamp=104.0))

        snapshot = state.snapshot(now=105.0)
        self.assertEqual(snapshot["current_phase_damage"], 500)
        self.assertEqual(snapshot["live_current_boss_damage"], 500)
        self.assertEqual(snapshot["live_session_total_damage"], 500)
        self.assertEqual(snapshot["current_phase_damage_taken"], 75)
        self.assertEqual(snapshot["recent_5s_dps"], 100.0)
        self.assertEqual(snapshot["recent_3s_dps"], 100.0)
        self.assertEqual(snapshot["current_phase_elapsed"], 5.0)

        expired = state.snapshot(now=110.0)
        self.assertEqual(expired["recent_5s_dps"], 0.0)
        self.assertEqual(expired["current_phase_damage"], 500)

    def test_recent_dps_tracks_damage_outside_boss_battles(self):
        state = EclipticaState()
        state.apply(self.make_event("stage", "Bringer", "1", "Thaumaturge", timestamp=100.0))

        self.assertTrue(state.apply(self.make_event("damage_dealt", "150", "STRIKE", timestamp=101.0)))
        self.assertTrue(state.apply(self.make_event("damage_dealt", "350", "STRIKE", timestamp=104.0)))

        snapshot = state.snapshot(now=105.0)
        self.assertEqual(snapshot["current_boss"], "-")
        self.assertEqual(snapshot["current_phase_damage"], 0)
        self.assertAlmostEqual(snapshot["recent_5s_dps"], 350 / 3)
        self.assertAlmostEqual(snapshot["recent_3s_dps"], 350 / 3)

        expired = state.snapshot(now=110.0)
        self.assertEqual(expired["recent_5s_dps"], 0.0)

    def test_new_boss_phase_has_independent_live_totals(self):
        state = EclipticaState()
        state.apply(self.make_event("boss", "JimBringer(Clone)", "0.5", timestamp=10.0))
        state.apply(self.make_event("damage_dealt", "1000", "STRIKE", timestamp=11.0))
        state.apply(self.make_event("damage_taken", "40", "attack_chop", timestamp=12.0))
        state.apply(self.make_event("boss", "JimBringerPhase2(Clone)", "0.8", timestamp=20.0))

        snapshot = state.snapshot(now=21.0)
        self.assertEqual(snapshot["current_phase_damage"], 0)
        self.assertEqual(snapshot["current_phase_damage_taken"], 0)
        self.assertEqual(snapshot["recent_5s_dps"], 0.0)
        self.assertEqual(snapshot["current_phase_elapsed"], 1.0)

    def test_repeated_identical_bosses_use_independent_damage_and_timing(self):
        state = EclipticaState()
        state.apply(self.make_event("stage", "Bringer", "0.1", "Thaumaturge", timestamp=1.0))
        state.apply(self.make_event("boss", "Despair(Clone)", "0.5", timestamp=10.0))
        state.apply(self.make_event("boss_dead", "Despair", timestamp=20.0))
        state.apply(self.make_event("strike_damage", "1000", timestamp=20.0))
        state.apply(self.make_event("non_strike_damage", "0", timestamp=20.0))
        state.apply(self.make_event("intermission", timestamp=21.0))
        state.apply(self.make_event("stage", "Bringer", "0.6", "Thaumaturge", timestamp=22.0))
        state.apply(self.make_event("boss", "Despair(Clone)", "0.7", timestamp=30.0))
        state.apply(self.make_event("damage_dealt", "250", "STRIKE", timestamp=34.0))

        live = state.snapshot(now=35.0)
        self.assertEqual(live["current_phase_damage"], 250)
        self.assertEqual(live["current_boss_damage"], 0)
        self.assertEqual(live["current_boss_elapsed"], 5.0)

        state.apply(self.make_event("boss_dead", "Despair", timestamp=40.0))
        state.apply(self.make_event("strike_damage", "2000", timestamp=40.0))
        state.apply(self.make_event("non_strike_damage", "0", timestamp=40.0))
        state.apply(self.make_event("intermission", timestamp=41.0))

        self.assertEqual([row["total"] for row in state.settlements], [2000, 1000])
        self.assertEqual([row["duration"] for row in state.settlements], [10.0, 10.0])
        self.assertEqual(state.session_total_damage, 3000)
        self.assertEqual(state.snapshot(now=41.0)["defeated_count"], 2)

    def test_boss_elapsed_crosses_phases_and_total_elapsed_stops_in_lobby(self):
        state = EclipticaState()
        state.apply(self.make_event("stage", "Bringer", "0.1", "Thaumaturge", timestamp=100.0))
        state.apply(self.make_event("boss", "Despair(Clone)", "0.5", timestamp=110.0))
        state.apply(self.make_event("boss", "DespairPhase2(Clone)", "0.8", timestamp=120.0))

        active = state.snapshot(now=125.0)
        self.assertEqual(active["current_phase_elapsed"], 5.0)
        self.assertEqual(active["current_boss_elapsed"], 15.0)
        self.assertEqual(active["total_elapsed"], 25.0)

        state.apply(self.make_event("lobby", timestamp=150.0))
        finished = state.snapshot(now=200.0)
        self.assertEqual(finished["current_boss_elapsed"], 0.0)
        self.assertEqual(finished["total_elapsed"], 50.0)

    def test_phase_two_keeps_current_boss_total_and_intermission_counts_once(self):
        state = EclipticaState()
        state.apply(self.make_event("boss", "Despair(Clone)", "0.5", timestamp=10.0))
        state.apply(self.make_event("boss_dead", "Despair", timestamp=20.0))
        state.apply(self.make_event("strike_damage", "1000", timestamp=20.0))
        state.apply(self.make_event("non_strike_damage", "100", timestamp=20.0))
        state.apply(self.make_event("boss", "DespairPhase2(Clone)", "0.5", timestamp=21.0))
        state.apply(self.make_event("boss_dead", "DespairPhase2", timestamp=31.0))
        state.apply(self.make_event("strike_damage", "2000", timestamp=31.0))
        state.apply(self.make_event("non_strike_damage", "200", timestamp=31.0))

        self.assertEqual(state.current_boss_damage, 3300)
        self.assertEqual(state.current_boss_phase, 2)
        state.apply(self.make_event("intermission", timestamp=32.0))
        state.apply(self.make_event("intermission", timestamp=33.0))
        self.assertEqual(state.snapshot(now=33.0)["defeated_count"], 1)

    def test_previous_phase_duration_survives_next_phase_starting_before_settlement(self):
        state = EclipticaState()
        state.apply(self.make_event("boss", "JimBringerPhase2(Clone)", "0.5", timestamp=100.0))
        state.apply(self.make_event("boss", "JimBringerPhase3(Clone)", "1", timestamp=130.0))
        state.apply(self.make_event("boss_dead", "JimBringerPhase2", timestamp=130.0))
        state.apply(self.make_event("strike_damage", "3000", timestamp=130.0))
        state.apply(self.make_event("non_strike_damage", "0", timestamp=130.0))

        self.assertEqual(state.settlements[0]["phase"], 2)
        self.assertEqual(state.settlements[0]["duration"], 30.0)
        self.assertEqual(state.settlements[0]["dps"], 100.0)

    def test_lobby_completes_final_boss_without_intermission(self):
        state = EclipticaState()
        state.apply(self.make_event("boss", "JimBringerPhase3(Clone)", "1", timestamp=10.0))
        state.apply(self.make_event("boss_dead", "JimBringerPhase3", timestamp=20.0))
        state.apply(self.make_event("strike_damage", "49307", timestamp=20.0))
        state.apply(self.make_event("non_strike_damage", "9960", timestamp=20.0))
        state.apply(self.make_event("lobby", timestamp=21.0))

        snapshot = state.snapshot(now=21.0)
        self.assertEqual(snapshot["defeated_count"], 1)
        self.assertEqual(snapshot["current_boss"], "-")
        self.assertTrue(snapshot["intermission"])

    def test_duplicate_settlement_is_not_counted_twice(self):
        state = EclipticaState()
        state.apply(self.make_event("boss", "M41D(Clone)", "0.5", timestamp=10.0))
        for event_time in (20.0, 25.0):
            state.apply(self.make_event("boss_dead", "M41D", timestamp=event_time))
            state.apply(self.make_event("strike_damage", "4000", timestamp=event_time))
            state.apply(self.make_event("non_strike_damage", "500", timestamp=event_time))
        self.assertEqual(state.session_total_damage, 4500)
        self.assertEqual(len(state.settlements), 1)

    def test_boss_ownership_drives_exact_aggro_player(self):
        state = EclipticaState()
        state.apply(self.make_event("authenticated", "Local Player", "usr_local", timestamp=1.0))
        state.apply(self.make_event("boss", "JimBringer(Clone)", "1", timestamp=10.0))

        unknown = state.aggro_snapshot(now=12.0)
        self.assertEqual(unknown["state"], "unknown")
        self.assertEqual(unknown["target"], "-")
        self.assertEqual(unknown["status"], "等待锁定目标")

        state.apply(self.make_event("ownership", "JimBringer", "Local Player", timestamp=20.0))

        local = state.aggro_snapshot(now=24.0)
        self.assertTrue(local["is_local"])
        self.assertEqual(local["state"], "local")
        self.assertEqual(local["target"], "Local Player")

        state.apply(self.make_event("ownership", "JimBringer", "Other Player", timestamp=29.0))
        other = state.aggro_snapshot(now=30.0)
        self.assertFalse(other["is_local"])
        self.assertEqual(other["state"], "other")
        self.assertEqual(other["target"], "Other Player")
        self.assertEqual(other["status"], "追击其他玩家")
        stale = state.aggro_snapshot(now=38.0)
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["state"], "other")
        self.assertEqual(stale["target"], "Other Player")

    def test_same_owner_refreshes_state_without_resetting_lock_duration(self):
        state = EclipticaState()
        state.apply(self.make_event("boss", "ObisidusPhase2(Clone)", "1", timestamp=10.0))
        state.apply(self.make_event("ownership", "ObisidusPhase2", "Player A", timestamp=20.0))
        state.apply(self.make_event("ownership", "ObisidusPhase2", "Player A", timestamp=25.0))

        aggro = state.aggro_snapshot(now=26.0)
        self.assertEqual(aggro["target"], "Player A")
        self.assertEqual(aggro["locked_secs"], 6)
        self.assertFalse(aggro["stale"])

    def test_ownership_from_another_boss_or_phase_is_ignored(self):
        state = EclipticaState()
        state.apply(self.make_event("boss", "ObisidusPhase2(Clone)", "1", timestamp=10.0))

        self.assertFalse(state.apply(self.make_event("ownership", "Obisidus", "Wrong Phase", timestamp=20.0)))
        self.assertFalse(state.apply(self.make_event("ownership", "ObisidusLightning", "Wrong Object", timestamp=21.0)))
        self.assertEqual(state.aggro_snapshot(now=22.0)["target"], "-")

    def test_damage_taken_does_not_replace_exact_ownership_target(self):
        state = EclipticaState()
        state.apply(self.make_event("boss", "JimBringer(Clone)", "1", timestamp=10.0))
        state.apply(self.make_event("ownership", "JimBringer", "Other Player", timestamp=20.0))
        state.apply(self.make_event("damage_taken", "43", "(Jim) attack_chop", timestamp=21.0))

        aggro = state.aggro_snapshot(now=22.0)
        self.assertEqual(aggro["target"], "Other Player")
        self.assertEqual(state.session_damage_taken, 43)
        self.assertEqual(state.max_hit_taken, 43)

    def test_new_session_id_resets_previous_session_totals(self):
        state = EclipticaState()
        state.apply(self.make_event("session", "1005", timestamp=1.0))
        state.session_total_damage = 1234
        state.apply(self.make_event("session", "24172", timestamp=2.0))
        self.assertEqual(state.session_id, "24172")
        self.assertEqual(state.session_total_damage, 0)

    def test_blank_session_stops_sync_identity_and_clears_previous_totals(self):
        state = EclipticaState()
        state.apply(self.make_event("authenticated", "Alice", "usr_alice", timestamp=1.0))
        state.apply(self.make_event("session", "24172", timestamp=2.0))
        state.session_total_damage = 1234

        state.apply(self.make_event("session_blank", timestamp=3.0))
        snapshot = state.snapshot(now=3.0)

        self.assertEqual(snapshot["session_id"], "-")
        self.assertEqual(snapshot["session_total_damage"], 0)
        self.assertEqual(snapshot["local_player_name"], "Alice")
        self.assertEqual(snapshot["local_player_id"], "usr_alice")

    def test_reentering_ecliptica_room_preserves_same_session(self):
        state = EclipticaState()
        state.apply(self.make_event("session", "24172", timestamp=1.0))
        state.session_total_damage = 1234
        state.apply(self.make_event("room_entered", "Ecliptica - Demo Playtest", timestamp=2.0))
        self.assertEqual(state.session_id, "24172")
        self.assertEqual(state.session_total_damage, 1234)


if __name__ == "__main__":
    unittest.main()
