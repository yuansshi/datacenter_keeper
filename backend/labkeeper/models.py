"""ORM models.

Shape borrowed from NetBox's DCIM (site/rack/device + change log), with testbeds and bookings
added on top. Bookings lock a testbed's *exclusive* devices, so testbeds that share hardware
cannot be booked at the same time (the labgrid "place" idea, applied to pyATS testbeds).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .timeutil import utcnow


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, always returns aware UTC datetimes (SQLite drops tzinfo)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


class Network(Base):
    """A logical network: a Meraki network, or a grouping declared in YAML."""

    __tablename__ = "networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="yaml")
    org_id: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(500))
    product_types: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # Which cloud it lives in (Global, Canada, China, India, Federal...) and that cloud's API host.
    region: Mapped[str | None] = mapped_column(String(60))
    api_host: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    devices: Mapped[list[Device]] = relationship(back_populates="network")


class FloorPlan(Base):
    """A room drawn as a grid of cells (racks, benches, pillars...). Rendered in 3D by the UI."""

    __tablename__ = "floor_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    site: Mapped[str | None] = mapped_column(String(200))
    # Prefix joining a cell to a rack name, e.g. "B1_" makes cell C/03 the rack "B1_C03".
    rack_prefix: Mapped[str] = mapped_column(String(100), default="")
    rows: Mapped[list[Any]] = mapped_column(JSON, default=list)  # top -> bottom labels
    columns: Mapped[list[Any]] = mapped_column(JSON, default=list)  # left -> right labels
    # [{row, col, type, label, span_rows, span_cols, metric}]
    cells: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metric_label: Mapped[str | None] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(40), default="manual")  # manual | image | file | <plugin>
    source_image: Mapped[str | None] = mapped_column(String(500))
    # "<floor source plugin>:<site id>" when the layout is owned by an external system (synced)
    source_ref: Mapped[str | None] = mapped_column(String(200), index=True)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sync_error: Mapped[str | None] = mapped_column(String(500))
    source_extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    racks: Mapped[list[Rack]] = relationship(back_populates="floor_plan")


class Rack(Base):
    __tablename__ = "racks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    site: Mapped[str | None] = mapped_column(String(200))
    height: Mapped[int] = mapped_column(Integer, default=42)
    description: Mapped[str | None] = mapped_column(Text)
    floor_plan_id: Mapped[int | None] = mapped_column(ForeignKey("floor_plans.id", ondelete="SET NULL"))
    floor_row: Mapped[str | None] = mapped_column(String(20))
    floor_col: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20), default="yaml")  # yaml | manual | floorplan
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    devices: Mapped[list[Device]] = relationship(back_populates="rack")
    floor_plan: Mapped[FloorPlan | None] = relationship(back_populates="racks")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable identity across re-imports: serial, else name+address, else file#name.
    key: Mapped[str] = mapped_column(String(300), unique=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    alias: Mapped[str | None] = mapped_column(String(200))
    vendor: Mapped[str | None] = mapped_column(String(100))
    type: Mapped[str | None] = mapped_column(String(100))  # pyATS "type", e.g. meraki-ms
    os: Mapped[str | None] = mapped_column(String(100))
    platform: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100), index=True)
    serial: Mapped[str | None] = mapped_column(String(100), index=True)
    role: Mapped[str] = mapped_column(String(50), default="other")
    mgmt_ip: Mapped[str | None] = mapped_column(String(100))
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    is_virtual: Mapped[bool] = mapped_column(Boolean, default=False)  # logical stack, VM...
    stack_name: Mapped[str | None] = mapped_column(String(100))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    source_file: Mapped[str | None] = mapped_column(String(500))
    org_id: Mapped[str | None] = mapped_column(String(64))
    connections: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # secrets redacted
    custom: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # secrets redacted
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    location: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(40))
    network_id: Mapped[int | None] = mapped_column(ForeignKey("networks.id", ondelete="SET NULL"))
    rack_id: Mapped[int | None] = mapped_column(ForeignKey("racks.id", ondelete="SET NULL"))
    rack_position: Mapped[int | None] = mapped_column(Integer)  # lowest U occupied
    rack_face: Mapped[str] = mapped_column(String(10), default="front")
    u_height: Mapped[int] = mapped_column(Integer, default=1)
    placement_source: Mapped[str | None] = mapped_column(String(20))  # yaml | manual
    status: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    status_detail: Mapped[str | None] = mapped_column(String(500))
    status_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    live: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    network: Mapped[Network | None] = relationship(back_populates="devices")
    rack: Mapped[Rack | None] = relationship(back_populates="devices")
    parent: Mapped[Device | None] = relationship(remote_side=[id], back_populates="members")
    members: Mapped[list[Device]] = relationship(back_populates="parent")
    memberships: Mapped[list[TestbedDevice]] = relationship(back_populates="device", cascade="all, delete-orphan")


class Testbed(Base):
    __tablename__ = "testbeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(500), unique=True)  # source path relative to its root
    name: Mapped[str] = mapped_column(String(200), index=True)
    source_file: Mapped[str | None] = mapped_column(String(500))
    group: Mapped[str | None] = mapped_column(String(200))  # folder, e.g. "ca"
    description: Mapped[str | None] = mapped_column(Text)
    custom: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    topology: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    org_id: Mapped[str | None] = mapped_column(String(64))
    maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    maintenance_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    members: Mapped[list[TestbedDevice]] = relationship(back_populates="testbed", cascade="all, delete-orphan")
    bookings: Mapped[list[Booking]] = relationship(back_populates="testbed")


class TestbedDevice(Base):
    __tablename__ = "testbed_devices"
    __table_args__ = (UniqueConstraint("testbed_id", "device_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    testbed_id: Mapped[int] = mapped_column(ForeignKey("testbeds.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    exclusive: Mapped[bool] = mapped_column(Boolean, default=True)

    testbed: Mapped[Testbed] = relationship(back_populates="members")
    device: Mapped[Device] = relationship(back_populates="memberships")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (Index("ix_bookings_window", "starts_at", "ends_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    testbed_id: Mapped[int] = mapped_column(ForeignKey("testbeds.id", ondelete="CASCADE"), index=True)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    purpose: Mapped[str | None] = mapped_column(String(500))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_via: Mapped[str] = mapped_column(String(20), default="ui")  # ui | api | agent | mcp
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # None: the whole testbed. Otherwise only these devices (stacks already expanded to members).
    device_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)

    testbed: Mapped[Testbed] = relationship(back_populates="bookings")

    @property
    def effective_end(self) -> datetime:
        if self.released_at and self.released_at < self.ends_at:
            return self.released_at
        return self.ends_at

    def is_active(self, now: datetime) -> bool:
        return self.cancelled_at is None and self.starts_at <= now < self.effective_end

    def state(self, now: datetime) -> str:
        if self.cancelled_at is not None:
            return "cancelled"
        if now < self.starts_at:
            return "upcoming"
        if now < self.effective_end:
            return "active"
        return "released" if self.released_at else "finished"


class Event(Base):
    """Audit log / change log (who did what, when)."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_object", "object_type", "object_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(200), default="system")
    action: Mapped[str] = mapped_column(String(40))
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[int | None] = mapped_column(Integer)
    object_name: Mapped[str | None] = mapped_column(String(300))
    message: Mapped[str | None] = mapped_column(String(1000))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ImportSource(Base):
    __tablename__ = "import_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(700), unique=True)
    root: Mapped[str] = mapped_column(String(700))
    kind: Mapped[str] = mapped_column(String(20), default="testbed")  # testbed | base | invalid
    testbed_id: Mapped[int | None] = mapped_column(ForeignKey("testbeds.id", ondelete="SET NULL"))
    mtime: Mapped[float | None] = mapped_column(Float)
    device_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    last_imported_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
