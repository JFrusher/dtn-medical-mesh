"""The simulated world: nodes, mobility, radio contacts, and the action driver."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from itertools import combinations
from pathlib import Path

from node.core import NodeConfig, NodeCore
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
    SendFrame,
    SetTimer,
    Store,
    Timer,
)
from node.hw.radio import RadioKind
from node.hw.rtc import RtcError
from node.pki import Identity
from node.storage import STORAGE_KEY_SLOT, Storage
from simulation.mobility import Mobility
from simulation.radio_model import Link, RadioParams, channel, transfer_s
from simulation.scheduler import Scheduler
from simulation.virtual_hw import SeededEntropy, SoftwareSecureElement, VirtualRtc

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

    def boot(self, now: float) -> None:
        """Start the node from what is on disk (also used after a power cut)."""
        if self.identity is None:
            raise RuntimeError(f"{self.id} was never provisioned")
        self.core = NodeCore(self.config, self.storage.load(), self.identity)
        self.boot_at = now

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
    ) -> None:
        self.scheduler = scheduler
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.radio = radio or RadioParams()
        self.tick_s = tick_s
        self.nodes: dict[str, SimNode] = {}
        self.contacts: set[frozenset[str]] = set()  # links that are up
        self.links: dict[frozenset[str], Link] = {}
        self._connecting: set[frozenset[str]] = set()
        self.lost_frames = 0
        self.log: LogFn = log or (lambda t, msg: None)

    def add_node(self, config: NodeConfig, mobility: Mobility) -> SimNode:
        if config.node_id in self.nodes:
            raise ValueError(f"duplicate node id {config.node_id}")
        se = SoftwareSecureElement(SeededEntropy(f"{self.scheduler.seed}:{config.node_id}"))
        storage = Storage(self.data_dir / f"{config.node_id}.db", se.read_slot(STORAGE_KEY_SLOT))
        node = SimNode(config, mobility, storage, VirtualRtc(self.scheduler), se)
        self.nodes[config.node_id] = node
        return node

    def start(self) -> None:
        for node in self.nodes.values():
            node.boot(self.scheduler.now)
        self.scheduler.after(0, self._tick)

    def position(self, node_id: str) -> tuple[float, float]:
        return self.nodes[node_id].mobility.position(self.scheduler.now)

    def dispatch(self, node: SimNode, event_factory: Callable[..., Event]) -> None:
        """Build an event stamped with the node's own clocks, run it, apply the actions in order."""
        try:
            wall: float | None = node.rtc.read()
        except RtcError:
            wall = None
        event = event_factory(mono=self.scheduler.now - node.boot_at, wall=wall)
        for action in node.core.handle(event):
            self._apply(node, action)

    def _apply(self, node: SimNode, action: Action) -> None:
        match action:
            case Store() | Delete():
                node.storage.apply(action)
            case SendFrame():
                self._transmit(node.id, action)
            case Deliver():
                self.log(self.scheduler.now, f"DELIVERED at {node.id}")
                if node.on_deliver:
                    node.on_deliver(action.bundle)
            case SetTimer():
                at = node.boot_at + action.at_mono
                self.scheduler.schedule(
                    max(at, self.scheduler.now),
                    lambda: self.dispatch(node, partial(Timer, name=action.name)),
                )
            case Delivered():
                self.log(self.scheduler.now, f"ACKED at origin {node.id}")
                if node.on_delivered:
                    node.on_delivered(action.bundle_id)
            case DirectoryUpdate():
                if node.on_directory:
                    node.on_directory(action.doc, action.issuer)
            case Emit():
                pass

    def _transmit(self, src: str, action: SendFrame) -> None:
        if action.radio is RadioKind.LORA:
            self._broadcast_lora(src, action)
            return
        link = self.links.get(frozenset((src, action.peer_id)))
        if link is None or not link.up:
            self.lost_frames += 1
            return
        start = max(self.scheduler.now, link.busy_until.get(src, 0.0))
        done = start + transfer_s(len(action.frame), self.radio)
        link.busy_until[src] = done
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
        """v0: every node within LoRa range hears it. Airtime and duty cycle come in P5.S03."""
        here = self.position(src)
        for node_id in sorted(self.nodes):
            if (
                node_id != src
                and math.dist(here, self.position(node_id)) <= self.radio.lora_range_m
            ):
                node = self.nodes[node_id]
                self.scheduler.after(
                    0,
                    partial(
                        self.dispatch,
                        node,
                        partial(
                            FrameReceived, peer_id=src, radio=RadioKind.LORA, frame=action.frame
                        ),
                    ),
                )

    def _in_range(self, a: str, b: str) -> bool:
        return math.dist(self.position(a), self.position(b)) <= self.radio.wifi_range_m

    def _tick(self) -> None:
        for a, b in combinations(sorted(self.nodes), 2):
            pair = frozenset((a, b))
            link = self.links.setdefault(pair, Link())
            if self._in_range(a, b):
                if not link.up and pair not in self._connecting:
                    self._connecting.add(pair)
                    self.scheduler.after(self.radio.wifi_setup_s, partial(self._link_up, a, b))
            elif link.up:
                link.up = False
                link.generation += 1
                link.busy_until.clear()
                self.contacts.discard(pair)
                self._notify_contact(ContactDown, a, b)
        self.scheduler.after(self.tick_s, self._tick)

    def _link_up(self, a: str, b: str) -> None:
        pair = frozenset((a, b))
        self._connecting.discard(pair)
        if not self._in_range(a, b):
            return  # drove out of range before the link came up
        self.links[pair].up = True
        self.contacts.add(pair)
        self.log(self.scheduler.now, f"CONTACT {a} <-> {b}")
        self._notify_contact(ContactUp, a, b)

    def _notify_contact(self, kind: type[ContactUp | ContactDown], a: str, b: str) -> None:
        for x, y in ((a, b), (b, a)):
            self.dispatch(self.nodes[x], partial(kind, peer_id=y, radio=RadioKind.WIFI))
