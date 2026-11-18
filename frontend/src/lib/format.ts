import type { DeviceStatus, TestbedStatus } from "./types";

export const STATUS_LABEL: Record<DeviceStatus, string> = {
  online: "Online",
  alerting: "Alerting · up",
  offline: "Offline",
  dormant: "Dormant",
  unknown: "Unknown",
};

export const STATUS_COLOR: Record<DeviceStatus, string> = {
  online: "var(--ok)",
  alerting: "var(--warn)",
  offline: "var(--bad)",
  dormant: "var(--violet)",
  unknown: "var(--faint)",
};

export const TESTBED_LABEL: Record<TestbedStatus, string> = {
  free: "Free",
  in_use: "In use",
  partial: "Partly in use",
  blocked: "Blocked",
  maintenance: "Maintenance",
};

export const TESTBED_COLOR: Record<TestbedStatus, string> = {
  free: "var(--ok)",
  in_use: "var(--warn)",
  partial: "var(--info)",
  blocked: "var(--violet)",
  maintenance: "var(--idle)",
};

export const ROLE_LABEL: Record<string, string> = {
  switch: "Switch",
  access_point: "Access point",
  camera: "Camera",
  router: "Router",
  security_appliance: "Security appliance",
  server: "Server",
  jump_host: "Jump host",
  traffic_generator: "Traffic generator",
  terminal_server: "Terminal server",
  cellular_gateway: "Cellular gateway",
  sensor: "Sensor",
  other: "Other",
};

export function roleLabel(role: string): string {
  return ROLE_LABEL[role] ?? role.replace(/_/g, " ");
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - now;
  const abs = Math.abs(diff);
  const min = 60_000;
  const hour = 60 * min;
  const day = 24 * hour;
  if (abs < 45_000) return diff >= 0 ? "in a moment" : "just now";
  if (abs < hour) return rtf.format(Math.round(diff / min), "minute");
  if (abs < day) return rtf.format(Math.round(diff / hour), "hour");
  return rtf.format(Math.round(diff / day), "day");
}

export function duration(ms: number): string {
  const totalMin = Math.max(0, Math.round(ms / 60_000));
  const d = Math.floor(totalMin / 1440);
  const h = Math.floor((totalMin % 1440) / 60);
  const m = totalMin % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return m ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

export function timeLeft(iso: string, now = Date.now()): string {
  return duration(new Date(iso).getTime() - now);
}

const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (sameDay(d, now)) return time;
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  if (sameDay(d, tomorrow)) return `Tomorrow ${time}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, yesterday)) return `Yesterday ${time}`;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function stamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function initials(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

/** Stable pastel-ish hue per owner so their bookings are recognisable at a glance. */
export function ownerHue(owner: string): number {
  let h = 0;
  for (let i = 0; i < owner.length; i++) h = (h * 31 + owner.charCodeAt(i)) % 360;
  return h;
}

export function plural(n: number, word: string, pluralWord?: string): string {
  return `${n} ${n === 1 ? word : (pluralWord ?? `${word}s`)}`;
}

/** Availability helpers for compact summaries; keep the individual states visible alongside them. */
export const isUp = (s: DeviceStatus) => s === "online" || s === "alerting";
export const upCount = (h: { online: number; alerting: number }) => h.online + h.alerting;
export const downCount = (h: { offline: number; dormant: number }) => h.offline + h.dormant;
