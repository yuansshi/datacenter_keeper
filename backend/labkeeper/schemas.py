"""API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Ref(BaseModel):
    id: int
    name: str


class BookingOut(BaseModel):
    id: int
    testbed_id: int
    testbed_name: str
    owner: str
    purpose: str | None
    starts_at: datetime
    ends_at: datetime
    effective_end: datetime
    state: str
    created_via: str
    created_at: datetime
    released_at: datetime | None
    device_ids: list[int] = Field(default_factory=list)  # empty: the whole testbed
    devices_label: str | None = None  # e.g. "2M Stack, SW_7" for a device-level booking


class StatusCounts(BaseModel):
    online: int = 0
    alerting: int = 0
    offline: int = 0
    dormant: int = 0
    unknown: int = 0


class DeviceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    alias: str | None
    role: str
    vendor: str | None
    model: str | None
    serial: str | None
    type: str | None
    os: str | None
    platform: str | None
    status: str
    status_detail: str | None
    status_changed_at: datetime | None
    last_seen_at: datetime | None
    mgmt_ip: str | None
    network: Ref | None
    rack: Ref | None
    rack_position: int | None
    rack_face: str
    u_height: int
    is_shared: bool
    is_virtual: bool
    parent_id: int | None
    stack_name: str | None
    provider: str | None
    has_console: bool
    testbed_count: int
    in_use_by: str | None = None  # owner of an active booking covering this device
    removed: bool
    tags: list[str]
    stack_role: str | None = None  # active | standby | member, from the status provider
    stack_number: int | None = None  # switch number in its stack
    member_count: int = 0  # for a stack: members still tracked


class TestbedMembership(BaseModel):
    id: int
    name: str
    status: str
    exclusive: bool


class PortTag(BaseModel):
    testbed: str
    interface: str
    tag: str | None
    type: str | None
    device: str | None = None  # set on a stack's page: which member the port is on


class DeviceDetail(DeviceSummary):
    connections: dict[str, Any]
    custom: dict[str, Any]
    live: dict[str, Any]
    location: str | None
    source_file: str | None
    notes: str | None
    org_id: str | None
    placement_source: str | None
    status_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    testbeds: list[TestbedMembership]
    members: list[DeviceSummary]
    parent: Ref | None
    port_tags: list[PortTag]
    events: list[EventOut] = Field(default_factory=list)


class TestbedSummary(BaseModel):
    id: int
    name: str
    key: str
    group: str | None
    status: str
    current: BookingOut | None
    next: BookingOut | None
    blocked_by: list[BookingOut]
    partial: list[BookingOut] = Field(default_factory=list)  # active bookings of some of its devices
    busy_count: int = 0  # its exclusive devices held by an active booking (any testbed)
    device_count: int
    exclusive_count: int
    health: StatusCounts
    models: list[str]
    kind: str | None
    maintenance: bool
    maintenance_note: str | None
    org_id: str | None
    tags: list[str]
    removed: bool


class TestbedDevice(DeviceSummary):
    exclusive: bool


class ConsoleLine(BaseModel):
    device_id: int
    device: str
    connection: str
    protocol: str | None
    host: str | None
    port: int | None
    via: str | None  # jump host
    console_server: str | None
    member: int | None  # stack member number the line belongs to


class LinkEnd(BaseModel):
    device_id: int | None
    device: str
    interface: str
    tag: str | None = None


class TopologyLink(BaseModel):
    name: str
    ends: list[LinkEnd]


class TrafficPort(BaseModel):
    interface: str  # e.g. 1/9/1 = chassis 1, card 9, port 1
    tag: str | None
    media: str | None
    ipv4: str | None
    peer: LinkEnd | None  # the device port it is cabled to


class TrafficGenerator(BaseModel):
    device_id: int
    name: str
    type: str | None
    os: str | None
    api: str | None  # e.g. http://192.0.2.5:11009 (traffic-generator REST API)
    api_connection: str | None = None  # testbed connection behind it, for a reachability test
    chassis: str | None
    session: str | None
    details: dict[str, Any]  # custom.traffic_generator, as written in the testbed file
    ports: list[TrafficPort]


class TestbedDetail(TestbedSummary):
    description: str | None
    source_file: str | None
    custom: dict[str, Any]
    topology: dict[str, Any]
    devices: list[TestbedDevice]
    stacks: list[DeviceSummary] = Field(default_factory=list)  # stacks its devices belong to
    bookings: list[BookingOut]
    shares_with: list[Ref]
    consoles: list[ConsoleLine] = Field(default_factory=list)
    links: list[TopologyLink] = Field(default_factory=list)
    traffic: list[TrafficGenerator] = Field(default_factory=list)


class RackOut(BaseModel):
    id: int
    name: str
    site: str | None
    height: int
    description: str | None
    device_count: int
    used_units: int
    floor_plan_id: int | None
    floor_row: str | None
    floor_col: str | None
    health: StatusCounts
    in_use: int = 0  # devices currently in a booked testbed


class RackDevice(DeviceSummary):
    pass


class RackDetail(RackOut):
    devices: list[RackDevice]


class NetworkOut(BaseModel):
    id: int
    key: str
    name: str
    provider: str
    org_id: str | None
    url: str | None
    product_types: list[str]
    tags: list[str]
    device_count: int
    health: StatusCounts
    region: str | None = None  # cloud: Global, Canada, China, India, Federal...
    api_host: str | None = None
    dashboard_host: str | None = None
    reported_count: int = 0  # devices a status provider reports in this network
    testbeds: list[Ref] = Field(default_factory=list)  # testbeds whose file names this as their network
    has_key: bool | None = None  # can Labkeeper read this network's cloud (None: not a provider cloud)
    key_env: str | None = None  # variable to set when it cannot


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    actor: str
    action: str
    object_type: str
    object_id: int | None
    object_name: str | None
    message: str | None


DeviceDetail.model_rebuild()


class FloorCell(BaseModel):
    row: str
    col: str
    type: Literal["rack", "empty_rack", "bench", "open_space", "pillar", "storage", "unassigned", "wall", "door"]
    label: str | None = None
    span_rows: int = Field(default=1, ge=1, le=20)
    span_cols: int = Field(default=1, ge=1, le=20)
    metric: float | None = None


class FloorPlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    site: str | None = None
    rack_prefix: str = ""
    rows: list[str] = Field(min_length=1, max_length=200)
    columns: list[str] = Field(min_length=1, max_length=200)
    cells: list[FloorCell] = Field(default_factory=list, max_length=20000)
    metric_label: str | None = None
    notes: str | None = None


class FloorRackDevice(BaseModel):
    id: int
    name: str
    status: str
    position: int | None
    u_height: int
    in_use_by: str | None = None


class FloorRackLink(BaseModel):
    row: str
    col: str
    rack: RackOut | None
    rack_name: str
    devices: list[FloorRackDevice] = Field(default_factory=list)


class FloorPlanOut(FloorPlanIn):
    id: int
    source: str
    has_image: bool
    created_at: datetime
    updated_at: datetime
    racks: list[FloorRackLink]
    source_ref: str | None = None
    synced_at: datetime | None = None
    sync_error: str | None = None
    source_extra: dict[str, Any] = Field(default_factory=dict)


class FloorPlanSummary(BaseModel):
    id: int
    name: str
    site: str | None
    source_ref: str | None = None
    synced_at: datetime | None = None
    rows: int
    columns: int
    rack_cells: int
    linked_racks: int
    updated_at: datetime


# ---- requests ------------------------------------------------------------------------------------


class BookingCreate(BaseModel):
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=60 * 24 * 31)
    purpose: str | None = Field(default=None, max_length=500)
    # Reserve only these devices or stacks of the testbed; omit for the whole testbed.
    device_ids: list[int] | None = Field(default=None, max_length=500)


class BookingRelease(BaseModel):
    force: bool = False


class BookingExtend(BaseModel):
    minutes: int = Field(ge=5, le=60 * 24 * 14)
    force: bool = False


class DevicePatch(BaseModel):
    notes: str | None = None
    u_height: int | None = Field(default=None, ge=0, le=60)
    role: str | None = None
    tags: list[str] | None = None


class Placement(BaseModel):
    rack_id: int | None = None
    position: int | None = Field(default=None, ge=1, le=100)
    face: Literal["front", "rear"] = "front"


class RackCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    site: str | None = None
    height: int = Field(default=42, ge=1, le=100)
    description: str | None = None
    floor_plan_id: int | None = None
    floor_row: str | None = None
    floor_col: str | None = None


class RackPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    site: str | None = None
    height: int | None = Field(default=None, ge=1, le=100)
    description: str | None = None


class TestbedPatch(BaseModel):
    maintenance: bool | None = None
    maintenance_note: str | None = Field(default=None, max_length=500)
    description: str | None = None


class ConsoleTestRequest(BaseModel):
    connection: str | None = None
