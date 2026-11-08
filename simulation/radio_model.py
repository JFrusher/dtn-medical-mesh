"""Radio links between virtual nodes (spec §12.2)."""

import math
import random
from collections import deque
from dataclasses import dataclass, field

from simulation.assumptions import group


@dataclass(frozen=True)
class RadioParams:
    """Values come from simulation/assumptions.yaml; build with `RadioParams.defaults()`."""

    wifi_tx_dbm: float
    wifi_antenna_dbi: float
    wifi_pl1m_db: float
    wifi_ple: float
    wifi_shadow_sigma_db: float
    wifi_goodput_fraction: float
    wifi_max_bytes_per_s: float
    wifi_cs_range_m: float
    ble_adv_interval_s: float
    ble_scan_duty: float
    wifi_join_s: float
    antenna_hub_m: float
    antenna_site_m: float
    antenna_relay_m: float
    antenna_mule_m: float
    clutter_db_per_100m: float
    lora_tx_dbm: float
    lora_antenna_dbi: float
    lora_pl1m_db: float
    lora_ple: float
    lora_shadow_sigma_db: float
    lora_sf: int
    lora_bw_hz: float
    lora_cr: int  # coding rate 4/(4+cr)
    lora_duty_cycle: float
    # Gilbert-Elliott burst loss per link (per frame)
    p_good_to_bad: float
    p_bad_to_good: float
    loss_in_good: float
    loss_in_bad: float
    bit_flip_per_frame: float  # chance a delivered frame has one bit flipped

    @classmethod
    def defaults(cls, **overrides: float) -> "RadioParams":
        return cls(**{**group("radio"), **overrides})  # type: ignore[arg-type]


@dataclass
class Link:
    """A Wi-Fi contact. `generation` changes each time it goes down, so frames that were on
    the air or queued when it dropped are lost instead of arriving on a later contact."""

    generation: int = 0
    up: bool = False
    bad: bool = False  # Gilbert-Elliott channel state
    busy_until: float = 0.0  # half duplex: both directions queue on one channel
    shadow_db: float | None = None  # drawn when the pair comes within reach, kept for the pass
    rate: float = 0.0  # goodput in bytes/s at the current distance; 0 = out of reach


def lora_airtime_s(payload_bytes: int, sf: int, bw_hz: float, cr: int) -> float:
    """Semtech AN1200.13: 8-symbol preamble, explicit header, CRC on."""
    t_sym = (1 << sf) / bw_hz
    low_rate = 1 if t_sym > 0.016 else 0
    num = 8 * payload_bytes - 4 * sf + 28 + 16
    payload_symbols = 8 + max(math.ceil(num / (4 * (sf - 2 * low_rate))) * (cr + 4), 0)
    return (8 + 4.25) * t_sym + payload_symbols * t_sym


@dataclass
class DutyCycle:
    """A node's LoRa airtime over the last hour, against its regional budget."""

    used: deque[tuple[float, float]] = field(default_factory=deque)  # (sent_at, airtime)

    def admit(self, now: float, airtime: float, duty: float) -> bool:
        while self.used and self.used[0][0] <= now - 3600:
            self.used.popleft()
        if sum(a for _, a in self.used) + airtime > duty * 3600:
            return False
        self.used.append((now, airtime))
        return True


WIFI_WAVELENGTH_M = 299_792_458 / 2.437e9  # channel 6
LORA_WAVELENGTH_M = 299_792_458 / 868.1e6


# Semtech SX1276 datasheet table 13: sensitivity (dBm) at 125 kHz by spreading factor
# (the SX1262 is within about 1 dB).
LORA_SENSITIVITY_DBM = {7: -123.0, 8: -126.0, 9: -129.0, 10: -132.0, 11: -134.5, 12: -137.0}


def lora_mean_rx_dbm(d_m: float, p: RadioParams) -> float:
    return p.lora_tx_dbm + 2 * p.lora_antenna_dbi - path_loss_db(d_m, p.lora_pl1m_db, p.lora_ple)


def lora_reach_m(p: RadioParams) -> float:
    """Distance beyond which even a +3 sigma draw cannot be heard at this spreading factor."""
    budget = p.lora_tx_dbm + 2 * p.lora_antenna_dbi - LORA_SENSITIVITY_DBM[p.lora_sf]
    return float(10 ** ((budget + 3 * p.lora_shadow_sigma_db - p.lora_pl1m_db) / (10 * p.lora_ple)))


# IEEE 802.11n-2009 table 20-23: minimum receiver sensitivity (dBm) and PHY rate (Mbit/s)
# for MCS 0-7, 20 MHz, one spatial stream, 800 ns guard interval.
WIFI_MCS = (
    (-82, 6.5), (-79, 13), (-77, 19.5), (-74, 26), (-70, 39), (-66, 52), (-65, 58.5), (-64, 65)
)  # fmt: skip


def path_loss_db(d_m: float, pl1m_db: float, ple: float) -> float:
    """Log-distance path loss (Rappaport eq. 4.68)."""
    return pl1m_db + 10 * ple * math.log10(max(d_m, 1.0))


def wifi_mean_rx_dbm(d_m: float, p: RadioParams) -> float:
    return p.wifi_tx_dbm + 2 * p.wifi_antenna_dbi - path_loss_db(d_m, p.wifi_pl1m_db, p.wifi_ple)


def wifi_rate(rx_dbm: float, p: RadioParams) -> float:
    """Goodput (bytes/s) of the fastest MCS the signal supports; 0 below MCS 0."""
    phy = max((mbit for sens, mbit in WIFI_MCS if rx_dbm >= sens), default=0.0)
    return min(phy * 1e6 / 8 * p.wifi_goodput_fraction, p.wifi_max_bytes_per_s)


def wifi_reach_m(p: RadioParams) -> float:
    """Distance beyond which even a +3 sigma shadowing draw cannot reach MCS 0."""
    budget = p.wifi_tx_dbm + 2 * p.wifi_antenna_dbi - WIFI_MCS[0][0] + 3 * p.wifi_shadow_sigma_db
    return float(10 ** ((budget - p.wifi_pl1m_db) / (10 * p.wifi_ple)))


def ble_discovery_s(p: RadioParams, rng: random.Random) -> float:
    """Advertisements until one lands in the peer's scan window."""
    k = 1
    while rng.random() >= p.ble_scan_duty:
        k += 1
    return k * p.ble_adv_interval_s


def channel(link: Link, frame: bytes, params: RadioParams, rng: random.Random) -> bytes | None:
    """Pass a frame through the link's channel: None if lost, else possibly with a flipped bit."""
    flip = params.p_bad_to_good if link.bad else params.p_good_to_bad
    if rng.random() < flip:
        link.bad = not link.bad
    if rng.random() < (params.loss_in_bad if link.bad else params.loss_in_good):
        return None
    if frame and rng.random() < params.bit_flip_per_frame:
        i = rng.randrange(len(frame) * 8)
        corrupted = bytearray(frame)
        corrupted[i // 8] ^= 1 << (i % 8)
        return bytes(corrupted)
    return frame
