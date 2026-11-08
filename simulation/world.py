"""The simulated world: nodes, mobility, radio contacts, and the action driver."""

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from itertools import combinations
from pathlib import Path

from node import VERSION, crypto, node_toml
from node.bundle import Receipt, decode_receipt
from node.core import T_LEDGER, NodeConfig, NodeCore
from node.events import (
    Action,
    ContactDown,
    ContactUp,
    Delete,
    Deliver,
    Delivered,
    DirectoryUpdate,
    Emit,
    Event,
    FrameReceived,
    HealthTick,
    Install,
    PowerLow,
    SendFrame,
    SetTimer,
    Store,
    Timer,
)
from node.hw.radio import RadioKind
from node.hw.rtc import RtcError
from node.pki import Identity, Role
from node.storage import STORAGE_KEY_SLOT, Storage
from simulation.assumptions import group
from simulation.compute import cost_s, flash_bytes, profiles
from simulation.mobility import Mobility
from simulation.radio_model import (
    LORA_SENSITIVITY_DBM,
    LORA_WAVELENGTH_M,
    WIFI_WAVELENGTH_M,
    DutyCycle,
    Link,
    RadioParams,
    ble_discovery_s,
    channel,
    lora_airtime_s,
    lora_mean_rx_dbm,
    lora_reach_m,
    wifi_mean_rx_dbm,
    wifi_rate,
    wifi_reach_m,
)
from simulation.scheduler import Scheduler
from simulation.terrain import Terrain
from simulation.virtual_hw import (
    Battery,
    SeededEntropy,
    SoftwareSecureElement,
    VirtualRtc,
    enclosure_c,
)

POWER_TICK_S = 60.0
POWER_ON_PCT = 10.0

LogFn = Callable[[float, str], None]


@dataclass
class SimNode:
    config: NodeConfig
    mobility: Mobility
    storage: Storage
    rtc: VirtualRtc
    se: SoftwareSecureElement
    identity: Identity | None = None  # set by provisioning
    core: NodeCore = field(init=False)
    boot_at: float = 0.0
    on_deliver: Callable[[bytes], None] | None = None
    on_delivered: Callable[[bytes], None] | None = None
    on_directory: Callable[[bytes, bytes], None] | None = None
    on_boot: Callable[[], None] | None = None
    db_path: Path | None = None
    up: bool = False
    boots: int = 0
    battery: Battery | None = None  # None: mains powered
    core_class: type[NodeCore] = NodeCore  # HostileCore for attackers
    attacks: frozenset[str] = frozenset()
    external_power: bool = True
    busy_until: float = 0.0  # virtual time its current work finishes (spec §12.2 compute)
    version: str = VERSION  # software running (the active A/B slot)
    node_toml: str = ""  # the signed config written at provisioning; boot refuses anything else

    def boot(self, now: float) -> None:
        """Start the node from what is on disk (also used after a power cut)."""
        if self.identity is None:
            raise RuntimeError(f"{self.id} was never provisioned")
        config = node_toml.load(self.node_toml, self.identity.root_pubkey)  # ConfigError if not
        if config != self.config:
            raise node_toml.ConfigError(f"{self.id}: node.toml is not this node's config")
        self.core = self.core_class(config, self.storage.load(), self.identity)
        if self.attacks:
            self.core.attacks = self.attacks  # type: ignore[attr-defined]
        self.boot_at = now
        self.boots += 1
        self.up = True
        if self.on_boot:
            self.on_boot()

    @property
    def id(self) -> str:
        return self.config.node_id


class World:
    def __init__(
        self,
        scheduler: Scheduler,
        data_dir: Path,
        radio: RadioParams | None = None,
        tick_s: float = 1.0,
        log: LogFn | None = None,
        terrain: Terrain | None = None,
    ) -> None:
        self.terrain = terrain
        self.scheduler = scheduler
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.radio = radio or RadioParams.defaults()
        self._wifi_reach = wifi_reach_m(self.radio)
        self.tick_s = tick_s
        self.nodes: dict[str, SimNode] = {}
        self.contacts: set[frozenset[str]] = set()  # links that are up
        self.links: dict[frozenset[str], Link] = {}
        self._connecting: set[frozenset[str]] = set()
        self.lost_frames = 0
        self.wifi_bytes = 0
        self.peak_bytes: dict[str, int] = defaultdict(int)
        self.lora_over_budget = 0
        self.lora_log: list[tuple[float, str]] = []  # (time, sender) of each LoRa broadcast
        # Custody receipts as nodes commit them: (time, receiver node, receipt). The same signed
        # records an auditor could collect from real nodes; the map and journey view use them.
        self.receipts: list[tuple[float, str, Receipt]] = []
        self._duty: dict[str, DutyCycle] = defaultdict(DutyCycle)
        self._profiles = profiles()
        self.busy_s: dict[str, float] = defaultdict(float)  # CPU and storage time per node
        self.min_battery_pct: dict[str, float] = defaultdict(lambda: 100.0)
        self.broken_versions: set[str] = set()  # emulated: releases whose daemon fails to start
        self.flash_written: dict[str, int] = defaultdict(int)  # bytes to flash, for wear
        self.flash_per_min: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.log: LogFn = log or (lambda t, msg: None)

    def add_node(
        self, config: NodeConfig, mobility: Mobility, battery: Battery | None = None
    ) -> SimNode:
        if config.node_id in self.nodes:
            raise ValueError(f"duplicate node id {config.node_id}")
        se = SoftwareSecureElement(SeededEntropy(f"{self.scheduler.seed}:{config.node_id}"))
        storage = Storage(self.data_dir / f"{config.node_id}.db", se.read_slot(STORAGE_KEY_SLOT))
        node = SimNode(config, mobility, storage, VirtualRtc(self.scheduler), se)
        node.db_path = self.data_dir / f"{config.node_id}.db"
        node.battery = battery
        self.nodes[config.node_id] = node
        return node

    def power_off(self, node: SimNode) -> None:
        """Hard power loss: RAM state, open links and pending timers are gone; disk remains."""
        if not node.up:
            return
        node.up = False
        for pair, link in self.links.items():
            if node.id in pair and link.up:
                self._drop(pair)
                (other,) = pair - {node.id}
                self.dispatch(
                    self.nodes[other], partial(ContactDown, peer_id=node.id, radio=RadioKind.WIFI)
                )
        node.storage.close()
        self.log(self.scheduler.now, f"POWER OFF {node.id}")

    def power_on(self, node: SimNode) -> None:
        if node.up:
            return
        assert node.db_path is not None
        node.storage = Storage(node.db_path, node.se.read_slot(STORAGE_KEY_SLOT))
        self.log(self.scheduler.now, f"POWER ON {node.id}")
        node.boot(self.scheduler.now)

    def start(self) -> None:
        for node in self.nodes.values():
            node.boot(self.scheduler.now)
        self.scheduler.after(0, self._tick)
        for i, node_id in enumerate(sorted(self.nodes)):  # staggered so reports don't coincide
            self.schedule_health(node_id, (i + 1) / (len(self.nodes) + 1))
        if any(n.battery for n in self.nodes.values()):
            self.scheduler.after(0, self._power_tick)

    def _power_tick(self) -> None:
        """Integrate batteries; signal PowerLow on losing external power; cut or restore."""
        now, dt = self.scheduler.now, POWER_TICK_S
        for node in self.nodes.values():
            b = node.battery
            if b is None:
                continue
            moving = node.mobility.position(now) != node.mobility.position(now + dt)
            external = b.external(now, moving)
            if node.up and node.external_power and not external:
                self.dispatch(node, PowerLow)
            node.external_power = external
            b.step(now, dt, moving, active=any(node.id in p for p in self.contacts))
            self.min_battery_pct[node.id] = min(self.min_battery_pct[node.id], b.pct)
            if node.up and b.level_wh <= 0:
                self.power_off(node)
            elif not node.up and b.pct >= POWER_ON_PCT:
                self.power_on(node)
        self.scheduler.after(dt, self._power_tick)

    def schedule_health(self, node_id: str, phase: float = 0.5) -> None:
        """Health ticks every interval, starting `phase` of an interval from now."""
        self.scheduler.after(
            group("node")["health_every_s"] * phase, partial(self._health, node_id)
        )

    def _health(self, node_id: str) -> None:
        node = self.nodes[node_id]
        if node.up:
            battery = round(node.battery.pct, 1) if node.battery else None
            temp = round(enclosure_c(self.scheduler.now, node.config.role is Role.RELAY), 1)
            tick = partial(
                HealthTick,
                battery_pct=battery,
                temp_c=temp,
                version=node.version,
                external_power=node.external_power,
            )
            self.dispatch(node, tick)
        self.scheduler.after(group("node")["health_every_s"], partial(self._health, node_id))

    def position(self, node_id: str) -> tuple[float, float]:
        return self.nodes[node_id].mobility.position(self.scheduler.now)

    def dispatch(
        self, node: SimNode, event_factory: Callable[..., Event], *, answer_now: bool = False
    ) -> None:
        """Build an event stamped with the node's own clocks, run it, apply the actions in order.

        answer_now: a laptop's API call (spec §9.6) gets the core's answer, or its refusal as an
        exception, straight away; its actions still take effect after work already queued."""
        if not node.up:
            return  # a powered-off node hears nothing
        try:
            wall: float | None = node.rtc.read()
        except RtcError:
            wall = None
        now = self.scheduler.now
        if node.busy_until > now and not answer_now:  # busy with an earlier event: wait in line
            self.scheduler.schedule(node.busy_until, partial(self.dispatch, node, event_factory))
            return
        event = event_factory(mono=now - node.boot_at, wall=wall)
        signs, verifies = node.se.signs, crypto.verifies
        actions = node.core.handle(event)
        received = len(event.frame) if isinstance(event, FrameReceived) else 0
        cost = cost_s(
            self._profiles[node.config.role],
            node.se.signs - signs,
            crypto.verifies - verifies,
            received,
            actions,
        )
        node.busy_until = max(now, node.busy_until) + cost
        self.busy_s[node.id] += cost
        written = flash_bytes(actions)
        if written:
            self.flash_written[node.id] += written
            self.flash_per_min[node.id][int(now // 60)] += written
        boot = node.boots

        def take_effect() -> None:
            if node.up and node.boots == boot:  # a power cut while busy loses the work
                for action in actions:
                    self._apply(node, action)

        self.scheduler.schedule(node.busy_until, take_effect)

    def _apply(self, node: SimNode, action: Action) -> None:
        match action:
            case Store() | Delete():
                node.storage.apply(action)
                self.peak_bytes[node.id] = max(self.peak_bytes[node.id], node.core.used_bytes)
                if isinstance(action, Store) and action.table == T_LEDGER:
                    self.receipts.append(
                        (self.scheduler.now, node.id, decode_receipt(action.value))
                    )
            case SendFrame():
                self._transmit(node.id, action)
            case Deliver():
                self.log(self.scheduler.now, f"DELIVERED at {node.id}")
                if node.on_deliver:
                    node.on_deliver(action.bundle)
            case SetTimer():
                at = node.boot_at + action.at_mono
                boot = node.boots

                def fire(name: str = action.name) -> None:
                    if node.boots == boot:  # timers die with the boot that set them
                        self.dispatch(node, partial(Timer, name=name))

                self.scheduler.schedule(max(at, self.scheduler.now), fire)
            case Delivered():
                self.log(self.scheduler.now, f"ACKED at origin {node.id}")
                if node.on_delivered:
                    node.on_delivered(action.bundle_id)
            case DirectoryUpdate():
                if node.on_directory:
                    node.on_directory(action.doc, action.issuer)
            case Emit():
                pass
            case Install():
                self.scheduler.after(0, partial(self._install, node, action.version))

    def _install(self, node: SimNode, version: str) -> None:
        """A/B update: reboot into the new slot on probation. A version in `broken_versions`
        never reports healthy, so after probation the bootloader returns to the old slot."""
        previous = node.version
        self.log(self.scheduler.now, f"INSTALL {node.id} {previous} -> {version}")
        self.power_off(node)
        node.version = version
        if version not in self.broken_versions:
            self.power_on(node)
            return

        def rollback() -> None:
            node.version = previous
            self.log(self.scheduler.now, f"ROLLBACK {node.id} {version} -> {previous}")
            self.power_on(node)

        self.scheduler.after(group("node")["probation_s"], rollback)

    def _transmit(self, src: str, action: SendFrame) -> None:
        if action.radio is RadioKind.LORA:
            self._broadcast_lora(src, action)
            return
        self.wifi_bytes += len(action.frame)
        link = self.links.get(frozenset((src, action.peer_id)))
        if link is None or not link.up:
            self.lost_frames += 1
            return
        start = max(self.scheduler.now, link.busy_until)
        done = start + len(action.frame) * self._sharing(src, action.peer_id) / link.rate
        link.busy_until = done
        generation = link.generation

        def arrive() -> None:
            if not link.up or link.generation != generation:
                self.lost_frames += 1  # the contact ended before this frame got through
                return
            frame = channel(link, action.frame, self.radio, self.scheduler.rng)
            if frame is None:
                self.lost_frames += 1
                return
            self.dispatch(
                self.nodes[action.peer_id],
                partial(FrameReceived, peer_id=src, radio=action.radio, frame=frame),
            )

        self.scheduler.schedule(done, arrive)

    def _broadcast_lora(self, src: str, action: SendFrame) -> None:
        """Everyone in LoRa range hears it after its airtime, if the sender has duty budget."""
        r = self.radio
        airtime = lora_airtime_s(len(action.frame), r.lora_sf, r.lora_bw_hz, r.lora_cr)
        if not self._duty[src].admit(self.scheduler.now, airtime, r.lora_duty_cycle):
            self.lora_over_budget += 1
            return
        here, reach = self.position(src), lora_reach_m(r)
        heard = partial(FrameReceived, peer_id=src, radio=RadioKind.LORA, frame=action.frame)
        sigma = r.lora_shadow_sigma_db
        for node_id in sorted(self.nodes):
            d = math.dist(here, self.position(node_id))
            if node_id == src or d > reach:
                continue
            shadow = max(-3 * sigma, min(3 * sigma, self.scheduler.rng.gauss(0, sigma)))
            loss = shadow + self._terrain_db(src, node_id, LORA_WAVELENGTH_M)
            if lora_mean_rx_dbm(d, r) - loss >= LORA_SENSITIVITY_DBM[r.lora_sf]:
                self.scheduler.after(airtime, partial(self.dispatch, self.nodes[node_id], heard))
        self.lora_log.append((self.scheduler.now, src))

    def _update_rate(self, link: Link, a: str, b: str, d: float) -> None:
        """Received power from path loss and this pass's shadowing sets the link's goodput."""
        r = self.radio
        if d > self._wifi_reach:
            link.shadow_db, link.rate = None, 0.0  # a fresh draw on the next approach
            return
        if link.shadow_db is None:
            sigma = r.wifi_shadow_sigma_db
            link.shadow_db = max(-3 * sigma, min(3 * sigma, self.scheduler.rng.gauss(0, sigma)))
        loss = link.shadow_db + self._terrain_db(a, b, WIFI_WAVELENGTH_M)
        link.rate = wifi_rate(wifi_mean_rx_dbm(d, r) - loss, r)

    def _terrain_db(self, a: str, b: str, wavelength_m: float) -> float:
        if self.terrain is None:
            return 0.0
        r = self.radio
        height = {
            Role.HUB: r.antenna_hub_m,
            Role.SITE: r.antenna_site_m,
            Role.RELAY: r.antenna_relay_m,
            Role.MULE: r.antenna_mule_m,
        }
        return self.terrain.loss_db(
            self.position(a),
            self.position(b),
            height[self.nodes[a].config.role],
            height[self.nodes[b].config.role],
            wavelength_m,
            r.clutter_db_per_100m,
        )

    def _sharing(self, a: str, b: str) -> int:
        """Contacts whose midpoints are within carrier-sense range share one channel."""

        def mid(x: str, y: str) -> tuple[float, float]:
            (x1, y1), (x2, y2) = self.position(x), self.position(y)
            return ((x1 + x2) / 2, (y1 + y2) / 2)

        here = mid(a, b)
        return max(
            1,
            sum(
                math.dist(here, mid(*sorted(p))) <= self.radio.wifi_cs_range_m
                for p in self.contacts
            ),
        )

    def _drop(self, pair: frozenset[str]) -> None:
        link = self.links[pair]
        link.up = False
        link.generation += 1
        link.busy_until = 0.0
        self.contacts.discard(pair)

    def _tick(self) -> None:
        up = sorted(n for n, node in self.nodes.items() if node.up)
        pos = {n: self.position(n) for n in up}
        for a, b in combinations(up, 2):
            pair = frozenset((a, b))
            link = self.links.setdefault(pair, Link())
            self._update_rate(link, a, b, math.dist(pos[a], pos[b]))
            if link.rate:
                if not link.up and pair not in self._connecting:
                    self._connecting.add(pair)
                    delay = ble_discovery_s(self.radio, self.scheduler.rng) + self.radio.wifi_join_s
                    self.scheduler.after(delay, partial(self._link_up, a, b))
            elif link.up:
                self._drop(pair)
                self._notify_contact(ContactDown, a, b)
        self.scheduler.after(self.tick_s, self._tick)

    def _link_up(self, a: str, b: str) -> None:
        pair = frozenset((a, b))
        self._connecting.discard(pair)
        link = self.links[pair]
        self._update_rate(link, a, b, math.dist(self.position(a), self.position(b)))
        if not link.rate or not (self.nodes[a].up and self.nodes[b].up):
            return  # drove out of range, or lost power, before the link came up
        self.links[pair].up = True
        self.contacts.add(pair)
        self.log(self.scheduler.now, f"CONTACT {a} <-> {b}")
        self._notify_contact(ContactUp, a, b)

    def _notify_contact(self, kind: type[ContactUp | ContactDown], a: str, b: str) -> None:
        for x, y in ((a, b), (b, a)):
            self.dispatch(self.nodes[x], partial(kind, peer_id=y, radio=RadioKind.WIFI))
