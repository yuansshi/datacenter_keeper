export type DeviceStatus = "online" | "alerting" | "offline" | "dormant" | "unknown";
export type TestbedStatus = "free" | "in_use" | "partial" | "blocked" | "maintenance";

export interface Ref {
  id: number;
  name: string;
}

export interface StatusCounts {
  online: number;
  alerting: number;
  offline: number;
  dormant: number;
  unknown: number;
}

export interface Booking {
  id: number;
  testbed_id: number;
  testbed_name: string;
  owner: string;
  purpose: string | null;
  starts_at: string;
  ends_at: string;
  effective_end: string;
  state: "active" | "upcoming" | "released" | "finished" | "cancelled";
  created_via: string;
  created_at: string;
  released_at: string | null;
  device_ids: number[]; // empty: the whole testbed
  devices_label: string | null; // e.g. "Example stack, switch-7"
}

export interface DeviceSummary {
  id: number;
  name: string;
  alias: string | null;
  role: string;
  vendor: string | null;
  model: string | null;
  serial: string | null;
  type: string | null;
  os: string | null;
  platform: string | null;
  status: DeviceStatus;
  status_detail: string | null;
  status_changed_at: string | null;
  last_seen_at: string | null;
  mgmt_ip: string | null;
  network: Ref | null;
  rack: Ref | null;
  rack_position: number | null;
  rack_face: "front" | "rear";
  u_height: number;
  is_shared: boolean;
  is_virtual: boolean;
  parent_id: number | null;
  stack_name: string | null;
  provider: string | null;
  has_console: boolean;
  testbed_count: number;
  in_use_by: string | null;
  removed: boolean;
  tags: string[];
  stack_role: string | null;
  stack_number: number | null;
  member_count: number;
}

export interface Connection {
  protocol?: string;
  ip?: string;
  host?: string;
  port?: number;
  proxy?: string;
  console_server_ip?: string;
  member?: number;
  username?: string;
  is_console: boolean;
}

export interface EventItem {
  id: number;
  ts: string;
  actor: string;
  action: string;
  object_type: string;
  object_id: number | null;
  object_name: string | null;
  message: string | null;
}

export interface DeviceDetail extends DeviceSummary {
  connections: Record<string, Connection>;
  custom: Record<string, unknown>;
  live: Record<string, Record<string, unknown>>;
  location: string | null;
  source_file: string | null;
  notes: string | null;
  org_id: string | null;
  placement_source: string | null;
  status_checked_at: string | null;
  created_at: string;
  updated_at: string;
  testbeds: { id: number; name: string; status: TestbedStatus; exclusive: boolean }[];
  members: DeviceSummary[];
  parent: Ref | null;
  port_tags: { testbed: string; interface: string; tag: string | null; type: string | null; device?: string | null }[];
  events: EventItem[];
}

export interface TestbedSummary {
  id: number;
  name: string;
  key: string;
  group: string | null;
  status: TestbedStatus;
  current: Booking | null;
  next: Booking | null;
  blocked_by: Booking[];
  partial: Booking[]; // active bookings of some of its devices
  busy_count: number; // its devices held by an active booking
  device_count: number;
  exclusive_count: number;
  health: StatusCounts;
  models: string[];
  kind: string | null;
  maintenance: boolean;
  maintenance_note: string | null;
  org_id: string | null;
  tags: string[];
  removed: boolean;
}

export interface TestbedDetail extends TestbedSummary {
  description: string | null;
  source_file: string | null;
  custom: Record<string, unknown>;
  topology: Record<string, Record<string, { tag?: string; type?: string; link?: string; media?: string; ipv4?: string }>>;
  devices: (DeviceSummary & { exclusive: boolean })[];
  stacks: DeviceSummary[];
  bookings: Booking[];
  shares_with: Ref[];
  consoles: ConsoleLine[];
  links: { name: string; ends: LinkEnd[] }[];
  traffic: TrafficGenerator[];
}

export interface ConsoleLine {
  device_id: number;
  device: string;
  connection: string;
  protocol: string | null;
  host: string | null;
  port: number | null;
  via: string | null;
  console_server: string | null;
  member: number | null;
}

export interface LinkEnd {
  device_id: number | null;
  device: string;
  interface: string;
  tag: string | null;
}

export interface TrafficGenerator {
  device_id: number;
  name: string;
  type: string | null;
  os: string | null;
  api: string | null;
  api_connection: string | null;
  chassis: string | null;
  session: string | null;
  details: Record<string, unknown>;
  ports: { interface: string; tag: string | null; media: string | null; ipv4: string | null; peer: LinkEnd | null }[];
}

export interface RackSummary {
  id: number;
  name: string;
  site: string | null;
  height: number;
  description: string | null;
  device_count: number;
  used_units: number;
  floor_plan_id: number | null;
  floor_row: string | null;
  floor_col: string | null;
  health: StatusCounts;
  in_use: number;
}

export interface RackDetail extends RackSummary {
  devices: DeviceSummary[];
}

export interface Network {
  id: number;
  key: string;
  name: string;
  provider: string;
  org_id: string | null;
  url: string | null;
  product_types: string[];
  tags: string[];
  device_count: number;
  health: StatusCounts;
  region: string | null;
  api_host: string | null;
  dashboard_host: string | null;
  reported_count: number;
  testbeds: Ref[];
  has_key: boolean | null;
  key_env: string | null;
}

export type CellType =
  | "rack"
  | "empty_rack"
  | "bench"
  | "open_space"
  | "pillar"
  | "storage"
  | "unassigned"
  | "wall"
  | "door";

export interface FloorCell {
  row: string;
  col: string;
  type: CellType;
  label: string | null;
  span_rows: number;
  span_cols: number;
  metric: number | null;
}

export interface FloorPlanInput {
  name: string;
  site: string | null;
  rack_prefix: string;
  rows: string[];
  columns: string[];
  cells: FloorCell[];
  metric_label: string | null;
  notes: string | null;
}

export interface FloorPlan extends FloorPlanInput {
  id: number;
  source: string;
  has_image: boolean;
  source_ref: string | null;
  synced_at: string | null;
  sync_error: string | null;
  source_extra: { asset_counts?: Record<string, number>; kw_total?: number; lab_name?: string; source?: string } & Record<string, unknown>;
  created_at: string;
  updated_at: string;
  racks: {
    row: string;
    col: string;
    rack: RackSummary | null;
    rack_name: string;
    devices: { id: number; name: string; status: DeviceStatus; position: number | null; u_height: number; in_use_by: string | null }[];
  }[];
}

export interface FloorPlanSummary {
  id: number;
  name: string;
  site: string | null;
  source_ref: string | null;
  synced_at: string | null;
  rows: number;
  columns: number;
  rack_cells: number;
  linked_racks: number;
  updated_at: string;
}

export type RegionAvailability =
  | { state: "available"; counts: StatusCounts; checked_at: string; stale_after_seconds: number }
  | { state: "unavailable" | "not-configured" | "pending"; counts: null; checked_at: null; stale_after_seconds: number };

export interface ProviderRegion {
  name: string;
  host: string;
  has_key: boolean;
  key_env: string | null;
  source: string;
  orgs: number | null;
  devices: number;
  error: string | null;
  availability: RegionAvailability;
}

export interface ProviderHealth {
  name: string;
  label: string;
  enabled: boolean;
  disabled_reason: string | null;
  last_run: string | null;
  last_ok: string | null;
  last_error: string | null;
  last_duration_ms: number | null;
  device_count: number;
  regions?: ProviderRegion[];
}

export interface ImportSummary {
  files: number;
  testbeds: number;
  bases: number;
  invalid: number;
  devices: number;
  devices_added: number;
  devices_updated: number;
  devices_removed: number;
  testbeds_added: number;
  testbeds_removed: number;
  racks_added: number;
  errors: { file: string; error: string }[];
  finished_at: string | null;
}

export interface SystemInfo {
  server_time: string;
  version: string;
  demo: boolean;
  user: string;
  user_header: string;
  providers: ProviderHealth[];
  assistant: { available: boolean; reason: string | null; model: string };
  testbed_paths: string[];
  last_import: ImportSummary | null;
  mcp_url: string;
  booking: { default_duration_minutes: number; max_duration_days: number; allow_force_release: boolean; release_policy: "anyone" | "owner" };
}

export interface Stats {
  devices: {
    total: number;
    health: StatusCounts;
    roles: Record<string, number>;
    switches: number;
    unracked: number;
  };
  testbeds: { total: number; free: number; in_use: number; blocked: number; maintenance: number };
  racks: number;
  networks: number;
  floor_plans: number;
  my_bookings: Booking[];
  in_use: Booking[];
  upcoming: Booking[];
  attention: DeviceSummary[];
  events: EventItem[];
}

export interface ConsoleResult {
  connection: string;
  protocol: string;
  state: string;
  ok: boolean;
  message: string;
  endpoint: string;
  via: string | null;
  duration_ms: number;
  output: string;
  hints: string[];
}

export interface Port {
  id: string;
  name: string | null;
  enabled: boolean;
  status: string | null;
  is_uplink: boolean;
  speed: string | null;
  duplex: string | null;
  errors: string[];
  warnings: string[];
  clients: number | null;
  poe: boolean;
  power_wh: number | null;
  type: string | null;
  vlan: number | null;
  allowed_vlans: string | null;
  neighbor: string | null;
  neighbor_port: string | null;
  traffic_kbps: number | null;
  tags?: string[];
}

export interface DeviceLive {
  device: string;
  source: string | null;
  ports: Port[];
  error: string | null;
}

export interface ImportSource {
  path: string;
  root: string;
  relative: string;
  kind: "testbed" | "base" | "invalid";
  testbed_id: number | null;
  device_count: number;
  error: string | null;
  last_imported_at: string | null;
}

export interface SearchResults {
  devices: { id: number; name: string; hint: string }[];
  testbeds: { id: number; name: string; hint: string }[];
  racks: { id: number; name: string; hint: string }[];
  networks: { id: number; name: string; hint: string }[];
}

export interface FloorSource {
  name: string;
  label: string;
  can_sync: boolean;
  sync_disabled_reason: string | null;
  sync_interval: number;
  browser_export: { title: string; instructions: string; bookmarklet: string; snippet: string; url_hint: string | null; origins: string[] } | null;
}

export interface FloorSourceSite {
  id: string;
  name: string;
  group: string | null;
  detail: string | null;
}

export type ExtractResult =
  | { method: "vision" | "grid"; draft: FloorPlanInput; image_token: string | null }
  | { method: "source"; source: string; label: string; plans: { id: number; name: string; created: boolean; changes: number }[] };
