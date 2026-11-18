import { useMemo } from "react";
import { CELL_FILL, cellKey, maxMetric, rackColor, type ColorMode, type RackLink } from "../lib/floor";
import type { CellType, FloorCell, FloorPlanInput } from "../lib/types";
import { clsx } from "./ui";

/**
 * Flat grid of the room: a map in view mode (coloured like the 3D view) and the editor surface in
 * edit mode (click an empty square to paint, click a cell to select it).
 */
export function FloorGrid({
  plan,
  links,
  mode = "health",
  selected,
  onCellClick,
  onEmptyClick,
  editing,
  cellSize = 30,
}: {
  plan: FloorPlanInput;
  links?: Map<string, RackLink>;
  mode?: ColorMode;
  selected?: string | null;
  onCellClick?: (cell: FloorCell) => void;
  onEmptyClick?: (row: string, col: string) => void;
  editing?: boolean;
  cellSize?: number;
}) {
  const mm = useMemo(() => maxMetric(plan.cells), [plan.cells]);
  const ri = useMemo(() => new Map(plan.rows.map((r, i) => [r, i])), [plan.rows]);
  const ci = useMemo(() => new Map(plan.columns.map((c, i) => [c, i])), [plan.columns]);
  const covered = useMemo(() => {
    const s = new Set<string>();
    for (const cell of plan.cells) {
      const r0 = ri.get(cell.row);
      const c0 = ci.get(cell.col);
      if (r0 === undefined || c0 === undefined) continue;
      for (let r = r0; r < r0 + cell.span_rows; r++) for (let c = c0; c < c0 + cell.span_cols; c++) s.add(`${r}:${c}`);
    }
    return s;
  }, [plan.cells, ri, ci]);

  const cols = plan.columns.length;
  const rows = plan.rows.length;
  const label = 26;

  return (
    <div className="overflow-auto">
      <div
        className="relative inline-grid select-none rounded-lg border border-line bg-panel-2 p-1"
        style={{
          gridTemplateColumns: `${label}px repeat(${cols}, ${cellSize}px)`,
          gridTemplateRows: `${label}px repeat(${rows}, ${cellSize}px)`,
        }}
      >
        <div />
        {plan.columns.map((c, i) => (
          <div key={`h${c}`} className="flex items-center justify-center font-mono text-[10px] text-faint" style={{ gridColumn: i + 2, gridRow: 1 }}>
            {c}
          </div>
        ))}
        {plan.rows.map((r, i) => (
          <div key={`r${r}`} className="flex items-center justify-center font-mono text-[11px] font-semibold text-muted" style={{ gridColumn: 1, gridRow: i + 2 }}>
            {r}
          </div>
        ))}
        {/* empty squares (click to paint while editing) */}
        {plan.rows.flatMap((r, i) =>
          plan.columns.map((c, j) =>
            covered.has(`${i}:${j}`) ? null : (
              <button
                key={`e${r}${c}`}
                type="button"
                disabled={!editing}
                onClick={() => onEmptyClick?.(r, c)}
                className={clsx("m-px rounded-[3px] border border-dashed border-line/70", editing ? "hover:border-accent hover:bg-accent-soft" : "cursor-default")}
                style={{ gridColumn: j + 2, gridRow: i + 2 }}
                aria-label={`Empty ${r}${c}`}
              />
            ),
          ),
        )}
        {plan.cells.map((cell) => {
          const r = ri.get(cell.row);
          const c = ci.get(cell.col);
          if (r === undefined || c === undefined) return null;
          const key = cellKey(cell.row, cell.col);
          const link = links?.get(key);
          const isRack = cell.type === "rack" || cell.type === "empty_rack";
          const color = isRack ? rackColor(cell, link, mode, mm) : undefined;
          const offline = link?.devices.filter((d) => d.status === "offline").length ?? 0;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onCellClick?.(cell)}
              title={`${link?.rack_name ?? `${cell.row}${cell.col}`} · ${cell.type.replace("_", " ")}${cell.metric != null ? ` · ${cell.metric}` : ""}`}
              className={clsx(
                "relative m-px flex items-center justify-center overflow-hidden rounded-[4px] text-[9.5px] font-semibold transition-transform",
                (onCellClick && (isRack || editing)) && "hover:z-10 hover:scale-[1.06]",
                selected === key && "z-10 ring-2 ring-accent ring-offset-1 ring-offset-panel-2",
                cell.type === "empty_rack" && "border-2 border-line-strong",
                cell.type === "unassigned" && "hatch",
                cell.type === "bench" && "hatch",
              )}
              style={{
                gridColumn: `${c + 2} / span ${cell.span_cols}`,
                gridRow: `${r + 2} / span ${cell.span_rows}`,
                background: isRack ? (cell.type === "empty_rack" ? "transparent" : color) : cell.type === "pillar" ? "transparent" : CELL_FILL[cell.type as CellType],
                color: isRack && cell.type === "rack" ? "#0b1412" : "var(--muted)",
              }}
            >
              {cell.type === "empty_rack" ? (
                <svg viewBox="0 0 10 10" className="absolute inset-1 text-line-strong" preserveAspectRatio="none">
                  <path d="M0 0L10 10M10 0L0 10" stroke="currentColor" strokeWidth="0.7" />
                </svg>
              ) : cell.type === "pillar" ? (
                <span className="size-1/2 rounded-[2px] bg-idle shadow-inner" />
              ) : (
                <span className="truncate px-0.5">
                  {cell.metric != null ? cell.metric : cell.label ?? (cell.type === "rack" ? "" : cell.span_cols > 1 ? cell.type.replace("_", " ") : "")}
                </span>
              )}
              {offline > 0 && <span className="absolute right-0.5 top-0.5 size-1.5 rounded-full bg-bad ring-1 ring-white/70" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
