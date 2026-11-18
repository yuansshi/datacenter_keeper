"""Floor-plan source plugins.

A floor-plan source turns an external system's idea of a room (a DCIM tool, a facilities
database, an internal lab tracker) into a Labkeeper floor plan. It can do this two ways:

* **Live sync**: ``list_sites()`` + ``fetch(site_id)`` against the system's API. Labkeeper then
  re-fetches on a schedule (``sync_interval``) so layout and metrics (kW, occupancy...) stay current.
* **Browser export**: for systems that only accept a user's browser login (SSO), the plugin ships a
  bookmarklet that saves the current page's data as a JSON file. Dropping that file on the Floor plans
  page calls ``parse_export()``. No credentials ever reach Labkeeper.

Plugins register through the ``labkeeper.floor_sources`` entry point::

    [project.entry-points."labkeeper.floor_sources"]
    mydcim = "my_package:MyDcimSource"

A synced floor plan stores ``source_ref = "<source name>:<site id>"``; its cells are owned by the
source (replaced on every sync) while its name, site and rack prefix stay as the user set them.
"""

from __future__ import annotations

import logging
from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import entry_points
from typing import Any, ClassVar

from ..config import Settings
from ..schemas import FloorPlanIn

log = logging.getLogger(__name__)


class FloorSourceError(Exception):
    """Raised with a message that is safe to show to users (no secrets)."""


@dataclass
class SourceSite:
    id: str
    name: str
    group: str | None = None  # e.g. building or metro, for grouping in pickers
    detail: str | None = None


@dataclass
class SourcePlan:
    """What a source hands back for one site."""

    site_id: str
    plan: FloorPlanIn
    rack_prefix: str | None = None  # None: let Labkeeper guess from existing rack names
    fetched_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. capacity summary, shown as-is


@dataclass
class BrowserExport:
    """Bookmarklet code may contain the placeholders ``__LABKEEPER_URL__`` and ``__LABKEEPER_USER__``
    (JS expressions, already URL-encoded in ``bookmarklet``). The Labkeeper UI fills them in with its
    own origin and the user's name, so the bookmarklet can POST the export straight to
    ``/api/v1/floorplans/extract``; ``origins`` lists the page origins allowed to do that (CORS)."""

    title: str
    instructions: str
    bookmarklet: str  # a ``javascript:`` URL
    snippet: str  # the same code, for pasting into the browser console
    url_hint: str | None = None  # where to run it, e.g. "https://dcim.example.com/lab/view/<id>"
    origins: list[str] = field(default_factory=list)


class FloorPlanSource(ABC):
    name: ClassVar[str]
    label: ClassVar[str] = ""

    def __init__(self, settings: Settings):
        self.settings = settings

    # -- live sync (optional) ------------------------------------------------------------------------
    @property
    def can_sync(self) -> bool:
        """True when the source is configured for server-side access (URL, token...)."""
        return False

    @property
    def sync_disabled_reason(self) -> str | None:
        return None

    @property
    def sync_interval(self) -> float:
        return 900.0

    async def list_sites(self) -> list[SourceSite]:
        raise FloorSourceError(f"{self.label or self.name} does not support listing sites")

    async def fetch(self, site_id: str) -> SourcePlan:
        raise FloorSourceError(f"{self.label or self.name} does not support live sync")

    # -- browser export (optional) ------------------------------------------------------------------
    def browser_export(self) -> BrowserExport | None:
        return None

    def matches_export(self, data: Any) -> bool:
        """True if ``data`` (parsed JSON) is this source's export format."""
        return False

    def parse_export(self, data: Any) -> list[SourcePlan]:
        raise FloorSourceError(f"{self.label or self.name} has no export format")

    async def aclose(self) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        export = self.browser_export()
        return {
            "name": self.name,
            "label": self.label or self.name,
            "can_sync": self.can_sync,
            "sync_disabled_reason": self.sync_disabled_reason,
            "sync_interval": self.sync_interval,
            "browser_export": None
            if export is None
            else {
                "title": export.title,
                "instructions": export.instructions,
                "bookmarklet": export.bookmarklet,
                "snippet": export.snippet,
                "url_hint": export.url_hint,
                "origins": export.origins,
            },
        }


def load_floor_sources(settings: Settings) -> dict[str, FloorPlanSource]:
    sources: dict[str, FloorPlanSource] = {}
    try:
        eps = entry_points(group="labkeeper.floor_sources")
    except Exception:  # pragma: no cover
        eps = []
    for ep in eps:
        try:
            cls = ep.load()
            sources[ep.name] = cls(settings)
        except Exception as exc:  # a broken plugin must not take the server down
            log.warning("could not load floor-plan source %s: %s", ep.name, exc)
    return sources
