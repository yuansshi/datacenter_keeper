import type { CellType, FloorCell, FloorPlan, FloorPlanInput } from "./types";

export type ColorMode = "health" | "occupancy" | "bookings" | "metric";
export type RackLink = FloorPlan["racks"][number];

export const CELL_TYPES: { type: CellType; label: string; hint: string }[] = [
  { type: "rack", label: "Rack", hint: "Holds devices" },
  { type: "empty_rack", label: "Empty rack", hint: "Rack frame, nothing installed" },
  { type: "bench", label: "Bench", hint: "Work bench; usually spans columns" },
  { type: "storage", label: "Storage", hint: "Cabinet or shelving" },
  { type: "pillar", label: "Pillar", hint: "Building column" },
  { type: "open_space", label: "Open space", hint: "Usable floor" },
  { type: "unassigned", label: "Unassigned", hint: "Blocked-off area" },
  { type: "wall", label: "Wall", hint: "Wall segment" },
  { type: "door", label: "Door", hint: "Entrance" },
];

export const CELL_FILL: Record<CellType, string> = {
  rack: "var(--panel-3)",
  empty_rack: "transparent",
  bench: "color-mix(in srgb, var(--info) 22%, var(--panel-2))",
  storage: "color-mix(in srgb, var(--violet) 22%, var(--panel-2))",
  pillar: "var(--idle)",
  open_space: "color-mix(in srgb, var(--ink) 4%, transparent)",
  unassigned: "color-mix(in srgb, var(--ink) 10%, var(--panel-2))",
  wall: "var(--muted)",
  door: "color-mix(in srgb, var(--warn) 30%, transparent)",
};

export function cellKey(row: string, col: string) {
  return `${row}|${col}`;
}

export function linkMap(plan: Pick<FloorPlan, "racks">): Map<string, RackLink> {
  return new Map(plan.racks.map((r) => [cellKey(r.row, r.col), r]));
}

/** Colour for a rack cell in the chosen mode (CSS colour string). */
export function rackColor(cell: FloorCell, link: RackLink | undefined, mode: ColorMode, maxMetric: number): string {
  if (cell.type === "empty_rack") return "var(--line-strong)";
  if (mode === "metric") {
    if (cell.metric == null) return "var(--info)";
    const r = maxMetric > 0 ? cell.metric / maxMetric : 0;
    return r > 0.66 ? "var(--bad)" : r > 0.4 ? "var(--warn)" : "var(--ok)";
  }
  if (!link?.rack) return "var(--idle)";
  const devs = link.devices;
  if (mode === "occupancy") {
    const used = link.rack.used_units / Math.max(link.rack.height, 1);
    if (!devs.length) return "var(--line-strong)";
    return `color-mix(in srgb, var(--accent) ${Math.round(25 + used * 75)}%, var(--panel-3))`;
  }
  if (mode === "bookings") {
    if (!devs.length) return "var(--line-strong)";
    return devs.some((d) => d.in_use_by) ? "var(--warn)" : "var(--ok)";
  }
  if (!devs.length) return "var(--line-strong)";
  if (devs.some((d) => d.status === "offline" || d.status === "dormant")) return "var(--bad)";
  if (devs.every((d) => d.status === "online" || d.status === "alerting")) return "var(--ok)";  // alerting is up
  return "var(--info)";
}

export function maxMetric(cells: FloorCell[]): number {
  return cells.reduce((m, c) => (c.metric != null && c.metric > m ? c.metric : m), 0);
}

/** Guess the rack-name prefix that joins grid cells to existing racks (e.g. "B1_" for B1_C03). */
export function suggestPrefix(plan: Pick<FloorPlanInput, "rows" | "columns">, rackNames: string[]): string | null {
  const rows = new Set(plan.rows.map((r) => r.toUpperCase()));
  const cols = new Set(plan.columns.flatMap((c) => [c, /^\d+$/.test(c) ? String(Number(c)) : c, /^\d+$/.test(c) ? c.padStart(2, "0") : c]));
  const tally = new Map<string, number>();
  for (const name of rackNames) {
    const m = /^(.*?)([A-Za-z]{1,2})(\d{1,3})$/.exec(name);
    if (!m) continue;
    const [, prefix, row, col] = m;
    if (rows.has(row.toUpperCase()) && cols.has(col)) tally.set(prefix, (tally.get(prefix) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestN = 0;
  tally.forEach((n, p) => {
    if (n > bestN) {
      best = p;
      bestN = n;
    }
  });
  return best;
}

/** Cells occupying a position (top-left anchors with spans). */
export function occupancy(plan: Pick<FloorPlanInput, "rows" | "columns" | "cells">): Map<string, FloorCell> {
  const ri = new Map(plan.rows.map((r, i) => [r, i]));
  const ci = new Map(plan.columns.map((c, i) => [c, i]));
  const out = new Map<string, FloorCell>();
  for (const cell of plan.cells) {
    const r0 = ri.get(cell.row);
    const c0 = ci.get(cell.col);
    if (r0 === undefined || c0 === undefined) continue;
    for (let r = r0; r < r0 + cell.span_rows && r < plan.rows.length; r++)
      for (let c = c0; c < c0 + cell.span_cols && c < plan.columns.length; c++) out.set(cellKey(plan.rows[r], plan.columns[c]), cell);
  }
  return out;
}

export function blankPlan(name = "New floor plan", rows = 6, cols = 12): FloorPlanInput {
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  return {
    name,
    site: null,
    rack_prefix: "",
    rows: Array.from({ length: rows }, (_, i) => letters[i] ?? `R${i + 1}`),
    columns: Array.from({ length: cols }, (_, i) => String(i + 1).padStart(2, "0")),
    cells: [],
    metric_label: null,
    notes: null,
  };
}

export const MODE_LABEL: Record<ColorMode, string> = {
  health: "Device health",
  occupancy: "Rack fill",
  bookings: "In use",
  metric: "Metric",
};

export function legendFor(mode: ColorMode, metricLabel: string | null): { color: string; label: string }[] {
  switch (mode) {
    case "health":
      return [
        { color: "var(--ok)", label: "all up (alerting counts as up)" },
        { color: "var(--bad)", label: "a device is down" },
        { color: "var(--info)", label: "some status unknown" },
        { color: "var(--line-strong)", label: "no devices" },
        { color: "var(--idle)", label: "not linked" },
      ];
    case "occupancy":
      return [
        { color: "color-mix(in srgb, var(--accent) 30%, var(--panel-3))", label: "few U used" },
        { color: "var(--accent)", label: "full" },
        { color: "var(--line-strong)", label: "empty" },
      ];
    case "bookings":
      return [
        { color: "var(--warn)", label: "device in a booked testbed" },
        { color: "var(--ok)", label: "free" },
        { color: "var(--line-strong)", label: "empty" },
      ];
    case "metric":
      return [
        { color: "var(--ok)", label: `low ${metricLabel ?? ""}`.trim() },
        { color: "var(--warn)", label: "medium" },
        { color: "var(--bad)", label: "high" },
        { color: "var(--info)", label: "no value" },
      ];
  }
}
