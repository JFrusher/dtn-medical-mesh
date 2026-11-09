"""The journey film GIF: its story must be the one the emulator run actually told."""

import re
import struct
from pathlib import Path

import pytest

from simulation.scenario import load_scenario
from tools.film import (
    REFERRAL,
    SHOTS,
    Hop,
    animated_webp,
    caption,
    ease,
    first_chain,
    is_relay,
    legs,
    lit_slots,
    refusal_window,
    sim_time,
    typing_frames,
    verify_cast,
    write_film_scenario,
)


def hop(t: float, a: str, b: str, *slots: int) -> Hop:
    return Hop(t, a, b, frozenset(slots))


def test_first_chain_walks_back_from_earliest_arrival() -> None:
    hops = [
        hop(50, "VAN_EAST", "HOSPITAL", 1),
        hop(30, "AMBULANCE", "HOSPITAL", 1, 2),
        hop(20, "VAN_WEST", "AMBULANCE", 1),
        hop(10, "OUTREACH_BIKE", "VAN_WEST", 1),
        hop(5, "MURIETI", "OUTREACH_BIKE", 1),
    ]
    assert first_chain(hops, "MURIETI", "HOSPITAL") == [
        "MURIETI",
        "OUTREACH_BIKE",
        "VAN_WEST",
        "AMBULANCE",
        "HOSPITAL",
    ]


RELAY = ["MURIETI", "LIGHT_MOSHI_ROAD", "OUTREACH_BIKE", "AMBULANCE", "HOSPITAL"]


def test_is_relay_accepts_the_staged_route() -> None:
    assert is_relay(RELAY)


@pytest.mark.parametrize(
    "chain",
    [
        ["MURIETI", "OUTREACH_BIKE", "AMBULANCE", "HOSPITAL"],
        ["MURIETI", "LIGHT_MOSHI_ROAD", "AMBULANCE", "HOSPITAL"],
        ["MURIETI", "AMBULANCE", "HOSPITAL"],
    ],
)
def test_is_relay_rejects_other_routes(chain: list[str]) -> None:
    assert not is_relay(chain)


def test_legs_follow_the_chain_in_time_order() -> None:
    hops = [
        hop(5, "MURIETI", "LIGHT_MOSHI_ROAD", 1),
        hop(9, "LIGHT_MOSHI_ROAD", "OUTREACH_BIKE", 1),
        hop(20, "OUTREACH_BIKE", "AMBULANCE", 1),
        hop(60, "AMBULANCE", "HOSPITAL", 1),
    ]
    assert legs(hops, RELAY) == [("LIGHT_MOSHI_ROAD", 5), ("OUTREACH_BIKE", 9), ("AMBULANCE", 20)]


def test_refusal_window_finds_the_first_refusal_on_the_last_leg() -> None:
    samples = [(0.0, 2), (10.0, 2), (20.0, 2), (30.0, 5), (40.0, 8), (50.0, 8)]
    assert refusal_window(samples, start=15, end=200, length=15) == (30.0, 45.0, 6)


def test_refusal_window_ignores_refusals_before_the_carrier_had_the_message() -> None:
    samples = [(0.0, 2), (10.0, 5), (20.0, 5)]
    assert refusal_window(samples, start=15, end=200, length=15) is None


def test_refusal_window_stops_at_the_hospital() -> None:
    samples = [(0.0, 0), (10.0, 1), (20.0, 9)]
    assert refusal_window(samples, start=0, end=12, length=60) == (10.0, 12, 1)


def test_film_scenario_adds_forger_and_loss_and_keeps_arusha(tmp_path: Path) -> None:
    s = load_scenario(str(write_film_scenario(tmp_path)))
    forger = next(n for n in s.nodes if n.config.node_id == "FORGER")
    assert forger.attacks == frozenset({"forge_shards"})
    assert s.radio.loss_in_bad == 0.5
    assert s.radio.bit_flip_per_frame == 0
    assert s.geo == load_scenario("arusha").geo


def test_film_scenario_stages_one_older_message_for_the_forger(tmp_path: Path) -> None:
    s = load_scenario(str(write_film_scenario(tmp_path)))
    assert [(m.sender, m.recipient) for m in s.messages] == [("kisongo", "usa-river")]


def test_verify_cast_accepts_the_story() -> None:
    verify_cast(RELAY, forged_delta=3, opened="Mon 10:00:00", acked="Mon 11:00:00")


@pytest.mark.parametrize(
    ("chain", "forged", "opened", "acked"),
    [
        (["MURIETI", "AMBULANCE", "HOSPITAL"], 3, "t", "t"),
        (RELAY, 0, "t", "t"),
        (RELAY, 3, None, "t"),
        (RELAY, 3, "t", None),
    ],
)
def test_verify_cast_rejects_changed_run(
    chain: list[str], forged: int, opened: str | None, acked: str | None
) -> None:
    with pytest.raises(RuntimeError):
        verify_cast(chain, forged, opened, acked)


def test_runtime_is_31_seconds() -> None:
    assert sum(s.frames for s in SHOTS) == 465


def test_copy_obeys_ban_list() -> None:
    for shot in SHOTS:
        for line in filter(None, caption(shot.name, forged=7) or ()):  # notes can be empty
            assert "—" not in line
            assert not re.search(r"\bnot\b.*\bbut\b", line, re.I)
            assert line.count(".") <= 1
            assert line != line.upper()


def test_attack_caption_uses_the_real_count() -> None:
    pair = caption("attack", forged=7)
    assert pair is not None and "7" in pair[0]


def test_attack_caption_refuses_zero() -> None:
    with pytest.raises(ValueError):
        caption("attack", forged=0)


def test_slots_light_one_at_a_time_in_arrival_order() -> None:
    hops = [Hop(9, "AMBULANCE", "HOSPITAL", frozenset({9, 2, 5, 11}))]
    assert lit_slots(hops, "HOSPITAL", 0.0) == []
    assert lit_slots(hops, "HOSPITAL", 0.5) == [2, 5]
    assert lit_slots(hops, "HOSPITAL", 1.0) == [2, 5, 9, 11]


def test_slots_ignore_duplicates_and_other_receivers() -> None:
    hops = [
        Hop(1, "A", "HOSPITAL", frozenset({3})),
        Hop(2, "B", "HOSPITAL", frozenset({3})),
        Hop(3, "A", "VAN_WEST", frozenset({7})),
    ]
    assert lit_slots(hops, "HOSPITAL", 1.0) == [3]


def test_sim_time_interpolates() -> None:
    assert sim_time(5, [(0, 100.0), (10, 200.0)]) == 150.0


def test_sim_time_holds_past_the_last_beat() -> None:
    assert sim_time(20, [(0, 100.0), (10, 200.0)]) == 200.0


def test_typing_ends_on_the_referral_after_one_real_correction() -> None:
    frames = typing_frames(REFERRAL)
    assert frames[-1] == REFERRAL
    assert sum(not REFERRAL.startswith(f) for f in frames) == 1
    assert all(abs(len(a) - len(b)) <= 1 for a, b in zip(frames, frames[1:], strict=False))


def test_ease_starts_and_ends_still_and_clamps() -> None:
    assert ease(-1) == 0
    assert ease(0) == 0
    assert ease(0.5) == 0.5
    assert ease(1) == 1
    assert ease(2) == 1
    assert ease(0.1) < 0.1


def _still_webp(payload: bytes) -> bytes:
    size = struct.pack("<I", len(payload))
    body = b"WEBPVP8 " + size + payload + (b"\0" if len(payload) % 2 else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_animated_webp_loops_forever_with_every_frame_in_order() -> None:
    data = animated_webp([_still_webp(b"abc"), _still_webp(b"wxyz")], 1280, 720, 15)
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    assert struct.unpack("<I", data[4:8])[0] == len(data) - 8
    assert data[12:16] == b"VP8X" and data[20] & 0x02  # animation flag
    assert int.from_bytes(data[24:27], "little") == 1279
    assert int.from_bytes(data[27:30], "little") == 719
    assert data[30:34] == b"ANIM"
    assert struct.unpack("<H", data[42:44])[0] == 0  # loop forever
    assert data.count(b"ANMF") == 2
    assert b"abc" in data and data.index(b"abc") < data.index(b"wxyz")
    first = data.index(b"ANMF") + 8
    assert int.from_bytes(data[first + 12 : first + 15], "little") == 67  # 1000 / 15 ms


def test_animated_webp_strips_each_still_of_its_own_wrapper() -> None:
    plain = _still_webp(b"abc")
    wrapped = plain[:12] + b"VP8X" + struct.pack("<I", 10) + bytes(10) + plain[12:]
    data = animated_webp([wrapped], 1280, 720, 15)
    assert data.count(b"VP8X") == 1
    assert b"abc" in data


def test_animated_webp_keeps_only_picture_data_from_each_still() -> None:
    plain = _still_webp(b"abc")
    with_profile = plain[:12] + b"ICCP" + struct.pack("<I", 4) + b"prof" + plain[12:]
    data = animated_webp([with_profile], 1280, 720, 15)
    assert b"ICCP" not in data
    assert b"abc" in data
