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
from node.storage import Storage
from simulation.mobility import Mobility
from simulation.radio_model import RadioParams
from simulation.scheduler import Scheduler
from simulation.virtual_hw import VirtualRtc

LogFn = Callable[[float, str], None]


@dataclass
class SimNode:
    config: NodeConfig
    mobility: Mobility
    storage: Storage
    rtc: VirtualRtc
    core: NodeCore = field(init=False)
    boot_at: float = 0.0
    on_deliver: Callable[[bytes], None] | None = None
    on_delivered: Callable[[bytes], None] | None = None

    def __post_init__(self) -> None:
        self.core = NodeCore(self.config, self.storage.load())

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
        self.contacts: set[frozenset[str]] = set()
        self.log: LogFn = log or (lambda t, msg: None)

    def add_node(self, config: NodeConfig, mobility: Mobility) -> SimNode:
        if config.node_id in self.nodes:
            raise ValueError(f"duplicate node id {config.node_id}")
        storage = Storage(self.data_dir / f"{config.node_id}.db")
        node = SimNode(config, mobility, storage, VirtualRtc(self.scheduler))
        self.nodes[config.node_id] = node
        return node

    def start(self) -> None:
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
            case Emit():
                pass

    def _transmit(self, src: str, action: SendFrame) -> None:
        pair = frozenset((src, action.peer_id))

        def arrive() -> None:
            if pair not in self.contacts:
                return  # link went down while the frame was in flight
            self.dispatch(
                self.nodes[action.peer_id],
                partial(FrameReceived, peer_id=src, radio=action.radio, frame=action.frame),
            )

        self.scheduler.after(0, arrive)

    def _tick(self) -> None:
        for a, b in combinations(sorted(self.nodes), 2):
            pair = frozenset((a, b))
            in_range = math.dist(self.position(a), self.position(b)) <= self.radio.wifi_range_m
            if in_range and pair not in self.contacts:
                self.contacts.add(pair)
                self.log(self.scheduler.now, f"CONTACT {a} <-> {b}")
                self._notify_contact(ContactUp, a, b)
            elif not in_range and pair in self.contacts:
                self.contacts.discard(pair)
                self._notify_contact(ContactDown, a, b)
        self.scheduler.after(self.tick_s, self._tick)

    def _notify_contact(self, kind: type[ContactUp | ContactDown], a: str, b: str) -> None:
        for x, y in ((a, b), (b, a)):
            self.dispatch(self.nodes[x], partial(kind, peer_id=y, radio=RadioKind.WIFI))
