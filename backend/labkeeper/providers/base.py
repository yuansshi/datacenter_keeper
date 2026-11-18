"""Status provider plugin interface.

A provider turns "what devices do we have" into "how are they doing right now". Labkeeper ships
three (``meraki``, ``reachability``, ``demo``); others (SNMP, gNMI, NAPALM, NetBox, vendor clouds)
register through the ``labkeeper.providers`` entry point::

    [project.entry-points."labkeeper.providers"]
    mycloud = "my_package.provider:MyCloudProvider"

Each device row names its provider (``provider`` column, set by the importer or overridden with
``custom.labkeeper.provider`` in the testbed file).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import entry_points
from typing import Any, ClassVar
from urllib.parse import urlparse

from ..config import Settings

log = logging.getLogger(__name__)

STATUS_VALUES = ("online", "alerting", "offline", "dormant", "unknown")


@dataclass(frozen=True)
class DeviceRef:
    id: int
    key: str
    name: str
    serial: str | None
    mgmt_ip: str | None
    org_id: str | None
    model: str | None
    role: str
    type: str | None


@dataclass
class NetworkInfo:
    key: str
    name: str
    provider: str
    org_id: str | None = None
    url: str | None = None
    product_types: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    region: str | None = None  # the cloud it lives in, e.g. Global, Canada, Federal
    api_host: str | None = None


@dataclass
class NetworkDeclaration:
    network: NetworkInfo
    own: bool = True


@dataclass
class StackInfo:
    """The stack a device belongs to, as the provider sees it (e.g. a Meraki switch stack)."""

    key: str  # stable across polls, e.g. "meraki:<stack id>"
    name: str
    role: str | None = None  # this device's role: active | standby | member
    number: int | None = None  # this device's switch number in the stack
    # every member the provider knows, tracked in Labkeeper or not: serial, name, model, role, number
    members: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # provider-specific (virtual MAC, ...)


@dataclass
class StatusUpdate:
    status: str
    detail: str | None = None
    live: dict[str, Any] | None = None  # merged into Device.live
    network: NetworkInfo | None = None
    last_seen: datetime | None = None
    # Stack membership. stack_checked=True with stack=None means "the provider looked: not stacked".
    stack: StackInfo | None = None
    stack_checked: bool = False


class StatusProvider(ABC):
    name: ClassVar[str]
    label: ClassVar[str] = ""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return True

    @property
    def disabled_reason(self) -> str | None:
        return None

    @property
    def poll_interval(self) -> float:
        return 60.0

    @abstractmethod
    async def poll(self, devices: list[DeviceRef]) -> dict[str, StatusUpdate]:
        """Return updates keyed by ``DeviceRef.key``. Devices left out keep their last status."""

    async def device_live(self, device: DeviceRef) -> dict[str, Any] | None:
        """Optional on-demand detail for the device page (ports, clients, uplinks...)."""
        return None

    @classmethod
    def declared_networks(cls, custom: dict[str, Any]) -> list[NetworkDeclaration]:
        """Optional adapter interpretation of network declarations in a testbed."""
        return []

    @classmethod
    def external_host(cls, api_host: str) -> str | None:
        """Optional browser host for an adapter's API host."""
        return None

    def observe_testbeds(self, customs: list[dict[str, Any]]) -> None:
        """Called before each poll with every tracked testbed's ``custom`` block (secrets masked), for
        providers that take hints from testbed files (e.g. which Meraki cloud an org lives in)."""
        return None

    def details(self) -> dict[str, Any]:
        """Extra status for the settings page (e.g. per-region reachability)."""
        return {}

    async def aclose(self) -> None:
        return None


def provider_classes() -> dict[str, type[StatusProvider]]:
    from .demo import DemoProvider
    from .meraki import MerakiProvider
    from .reachability import ReachabilityProvider

    classes: dict[str, type[StatusProvider]] = {
        "meraki": MerakiProvider,
        "reachability": ReachabilityProvider,
        "demo": DemoProvider,
    }
    try:
        for ep in entry_points(group="labkeeper.providers"):
            if ep.name in classes:
                continue
            try:
                cls = ep.load()
                classes[ep.name] = cls
            except Exception as exc:  # pragma: no cover - plugin errors
                log.warning("could not load provider plugin %s: %s", ep.name, exc)
    except Exception:  # pragma: no cover
        pass
    return classes


def declared_networks(custom: dict[str, Any]) -> list[NetworkDeclaration]:
    return [network for cls in provider_classes().values() for network in cls.declared_networks(custom)]


def network_external_host(provider: str, api_host: str | None, url: str | None) -> str | None:
    if url:
        return urlparse(url).hostname
    cls = provider_classes().get(provider)
    return cls.external_host(api_host) if cls and api_host else None


def load_providers(settings: Settings) -> dict[str, StatusProvider]:
    return {name: cls(settings) for name, cls in provider_classes().items()}
