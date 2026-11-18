import type {
  Booking,
  ExtractResult,
  FloorSource,
  FloorSourceSite,
  ConsoleResult,
  DeviceDetail,
  DeviceLive,
  DeviceSummary,
  EventItem,
  FloorPlan,
  FloorPlanInput,
  FloorPlanSummary,
  ImportSource,
  ImportSummary,
  Network,
  RackDetail,
  RackSummary,
  SearchResults,
  Stats,
  SystemInfo,
  TestbedDetail,
  TestbedSummary,
} from "./types";
import { getUser } from "./user";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  const user = getUser();
  if (user) h.set("X-Labkeeper-User", user);
  return h;
}

async function request<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const isForm = body instanceof FormData;
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers: headers(isForm || body === undefined ? undefined : { "Content-Type": "application/json" }),
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    ...init,
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : data;
    let message = `Request failed (${res.status})`;
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object" && "message" in detail) message = String((detail as { message: unknown }).message);
    else if (Array.isArray(detail) && detail[0]?.msg) message = detail.map((d: { msg: string }) => d.msg).join("; ");
    throw new ApiError(res.status, message, detail);
  }
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const api = {
  system: () => request<SystemInfo>("GET", "/system"),
  stats: () => request<Stats>("GET", "/stats"),
  search: (q: string) => request<SearchResults>("GET", `/search${qs({ q })}`),

  devices: (params: { q?: string; status?: string; role?: string; network_id?: number; testbed_id?: number; rack_id?: number; unracked?: boolean; include_virtual?: boolean } = {}) =>
    request<DeviceSummary[]>("GET", `/devices${qs(params)}`),
  device: (id: number) => request<DeviceDetail>("GET", `/devices/${id}`),
  patchDevice: (id: number, body: Partial<{ notes: string; u_height: number; role: string; tags: string[] }>) =>
    request<DeviceDetail>("PATCH", `/devices/${id}`, body),
  placeDevice: (id: number, body: { rack_id: number | null; position: number | null; face?: "front" | "rear" }) =>
    request<DeviceSummary>("PUT", `/devices/${id}/placement`, body),
  resetPlacement: (id: number) => request<DeviceSummary>("POST", `/devices/${id}/placement/reset`),
  deviceLive: (id: number) => request<DeviceLive>("GET", `/devices/${id}/live`),
  consoleTest: (id: number, connection?: string) =>
    request<{ device: string; results: ConsoleResult[] }>("POST", `/devices/${id}/console-test`, { connection: connection ?? null }),
  refreshDevice: (id: number) => request<DeviceSummary>("POST", `/devices/${id}/refresh`),

  testbeds: (params: { q?: string; status?: string; group?: string; model?: string } = {}) =>
    request<TestbedSummary[]>("GET", `/testbeds${qs(params)}`),
  testbed: (id: number) => request<TestbedDetail>("GET", `/testbeds/${id}`),
  memberships: () =>
    request<{ testbed_id: number; name: string; group: string | null; device_ids: number[]; shared_ids: number[] }[]>("GET", "/testbeds/memberships"),
  patchTestbed: (id: number, body: Partial<{ maintenance: boolean; maintenance_note: string | null; description: string }>) =>
    request<TestbedDetail>("PATCH", `/testbeds/${id}`, body),
  testbedSource: (id: number) => request<{ files: { path: string; name: string; text: string }[] }>("GET", `/testbeds/${id}/source`),
  availability: (id: number, start: string | null, end: string | null, devices?: number[] | null) =>
    request<{ available: boolean; conflicts: Booking[]; error?: string; device_ids?: number[]; devices_label?: string | null }>(
      "GET",
      `/testbeds/${id}/availability${qs({ start, end, devices: devices?.length ? devices.join(",") : undefined })}`,
    ),
  book: (
    id: number,
    body: { starts_at?: string | null; ends_at?: string | null; duration_minutes?: number | null; purpose?: string | null; device_ids?: number[] | null },
  ) =>
    request<Booking>("POST", `/testbeds/${id}/bookings`, body),

  bookings: (params: { start?: string; end?: string; testbed_id?: number; owner?: string; active_only?: boolean } = {}) =>
    request<Booking[]>("GET", `/bookings${qs(params)}`),
  release: (id: number, force = false) => request<Booking>("POST", `/bookings/${id}/release`, { force }),
  extend: (id: number, minutes: number, force = false) => request<Booking>("POST", `/bookings/${id}/extend`, { minutes, force }),

  racks: () => request<RackSummary[]>("GET", "/racks"),
  rack: (id: number) => request<RackDetail>("GET", `/racks/${id}`),
  createRack: (body: { name: string; height: number; site?: string | null; description?: string | null }) =>
    request<RackDetail>("POST", "/racks", body),
  patchRack: (id: number, body: Partial<{ name: string; height: number; site: string | null; description: string | null }>) =>
    request<RackDetail>("PATCH", `/racks/${id}`, body),
  deleteRack: (id: number) => request<void>("DELETE", `/racks/${id}`),

  networks: () => request<Network[]>("GET", "/networks"),
  events: (params: { limit?: number; object_type?: string; object_id?: number; actor?: string; action?: string; before_id?: number } = {}) =>
    request<EventItem[]>("GET", `/events${qs(params)}`),

  imports: () =>
    request<{ roots: string[]; uploads_dir: string; watching: boolean; last_summary: ImportSummary | null; sources: ImportSource[] }>("GET", "/imports"),
  rescan: () => request<ImportSummary>("POST", "/imports/rescan"),
  uploadTestbeds: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return request<{ saved: string[]; summary: ImportSummary }>("POST", "/imports/upload", fd);
  },

  floorPlans: () => request<FloorPlanSummary[]>("GET", "/floorplans"),
  floorPlan: (id: number) => request<FloorPlan>("GET", `/floorplans/${id}`),
  createFloorPlan: (body: FloorPlanInput & { image_token?: string | null }) => request<FloorPlan>("POST", "/floorplans", body),
  updateFloorPlan: (id: number, body: FloorPlanInput & { image_token?: string | null }) =>
    request<FloorPlan>("PUT", `/floorplans/${id}`, body),
  deleteFloorPlan: (id: number) => request<void>("DELETE", `/floorplans/${id}`),
  extractFloorPlan: (file: File, hint?: string, name?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (hint) fd.append("hint", hint);
    if (name) fd.append("name", name);
    return request<ExtractResult>("POST", "/floorplans/extract", fd);
  },
  floorSources: () => request<FloorSource[]>("GET", "/floorsources"),
  floorSourceSites: (name: string) => request<FloorSourceSite[]>("GET", `/floorsources/${name}/sites`),
  importFromSource: (name: string, siteId: string) =>
    request<{ id: number; name: string; created: boolean; changes: number }>("POST", `/floorsources/${name}/import`, { site_id: siteId }),
  syncFloorPlan: (id: number) => request<{ id: number; name: string; changes: number; synced_at: string }>("POST", `/floorplans/${id}/sync`),
  detachFloorPlan: (id: number) => request<FloorPlan>("POST", `/floorplans/${id}/detach`),
  createRackForCell: (planId: number, row: string, col: string, height = 42) =>
    request<RackDetail>("POST", `/floorplans/${planId}/racks`, { row, col, height }),
};

export async function streamChat(
  body: {
    conversation_id: string | null;
    message: string;
    page: string;
    local_time: string;
    tz_offset_minutes: number;
    attachments: { name: string; media_type: string; data: string }[];
  },
  onEvent: (ev: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/v1/assistant/chat", {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new ApiError(res.status, `Assistant request failed (${res.status})`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch {
        /* ignore malformed event */
      }
    }
  }
}
