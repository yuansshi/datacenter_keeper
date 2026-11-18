import { Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { CELL_FILL, CELL_TYPES, cellKey, suggestPrefix } from "../lib/floor";
import type { CellType, FloorCell, FloorPlanInput } from "../lib/types";
import { FloorGrid } from "./FloorGrid";
import { Button, Field, Input, clsx } from "./ui";

function parseLabels(text: string): string[] {
  const t = text.trim();
  const range = /^([A-Za-z]|\d+)\s*(?:-|\.\.|–)\s*([A-Za-z]|\d+)$/.exec(t);
  if (range) {
    const [, a, b] = range;
    if (/^\d+$/.test(a) && /^\d+$/.test(b)) {
      const width = Math.max(a.length, b.length);
      const lo = Number(a);
      const hi = Number(b);
      const step = lo <= hi ? 1 : -1;
      const out: string[] = [];
      for (let i = lo; step > 0 ? i <= hi : i >= hi; i += step) out.push(String(i).padStart(width, "0"));
      return out;
    }
    const lo = a.toUpperCase().charCodeAt(0);
    const hi = b.toUpperCase().charCodeAt(0);
    const step = lo <= hi ? 1 : -1;
    const out: string[] = [];
    for (let i = lo; step > 0 ? i <= hi : i >= hi; i += step) out.push(String.fromCharCode(i));
    return out;
  }
  return t.split(/[,\s]+/).filter(Boolean);
}

export function FloorPlanEditor({
  plan,
  onChange,
  rackNames,
}: {
  plan: FloorPlanInput;
  onChange: (p: FloorPlanInput) => void;
  rackNames: string[];
}) {
  const [tool, setTool] = useState<CellType>("rack");
  const [spanCols, setSpanCols] = useState(1);
  const [spanRows, setSpanRows] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [rowsText, setRowsText] = useState(plan.rows.join(", "));
  const [colsText, setColsText] = useState(plan.columns.join(", "));
  const sel = plan.cells.find((c) => cellKey(c.row, c.col) === selected) ?? null;
  const suggested = useMemo(() => suggestPrefix(plan, rackNames), [plan, rackNames]);
  const counts = useMemo(() => {
    const m = new Map<CellType, number>();
    plan.cells.forEach((c) => m.set(c.type, (m.get(c.type) ?? 0) + 1));
    return m;
  }, [plan.cells]);

  const update = (cell: FloorCell, patch: Partial<FloorCell>) =>
    onChange({ ...plan, cells: plan.cells.map((c) => (c === cell ? { ...c, ...patch } : c)) });

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
      <div className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {CELL_TYPES.map((t) => (
            <button
              key={t.type}
              title={t.hint}
              onClick={() => setTool(t.type)}
              className={clsx(
                "flex h-8 items-center gap-1.5 rounded-lg border px-2 text-[12px] font-medium",
                tool === t.type ? "border-accent bg-accent-soft text-ink" : "border-line bg-panel-2 text-muted hover:text-ink",
              )}
            >
              <span
                className={clsx("size-3 rounded-[3px]", t.type === "empty_rack" && "border-2 border-line-strong", (t.type === "bench" || t.type === "unassigned") && "hatch")}
                style={{ background: t.type === "rack" ? "var(--ok)" : CELL_FILL[t.type] }}
              />
              {t.label}
              {counts.get(t.type) ? <span className="num text-[10.5px] text-faint">{counts.get(t.type)}</span> : null}
            </button>
          ))}
          <span className="ml-2 flex items-center gap-1 text-[11.5px] text-muted">
            span
            <Input type="number" min={1} max={20} value={spanCols} onChange={(e) => setSpanCols(Math.max(1, Number(e.target.value)))} className="w-14" aria-label="Columns spanned" />
            ×
            <Input type="number" min={1} max={20} value={spanRows} onChange={(e) => setSpanRows(Math.max(1, Number(e.target.value)))} className="w-14" aria-label="Rows spanned" />
          </span>
        </div>
        <FloorGrid
          plan={plan}
          editing
          selected={selected}
          onEmptyClick={(row, col) => {
            const cell: FloorCell = { row, col, type: tool, label: null, metric: null, span_cols: spanCols, span_rows: spanRows };
            onChange({ ...plan, cells: [...plan.cells, cell] });
            setSelected(cellKey(row, col));
          }}
          onCellClick={(cell) => setSelected(cellKey(cell.row, cell.col))}
        />
        <p className="text-[11.5px] text-faint">Click an empty square to place the selected type. Click a cell to edit or delete it.</p>
      </div>

      <div className="space-y-4">
        <div className="space-y-3 rounded-xl border border-line bg-panel-2/60 p-3">
          <Field label="Name">
            <Input value={plan.name} onChange={(e) => onChange({ ...plan, name: e.target.value })} />
          </Field>
          <Field label="Site / room">
            <Input value={plan.site ?? ""} onChange={(e) => onChange({ ...plan, site: e.target.value || null })} placeholder="e.g. Building A / Lab 2" />
          </Field>
          <Field
            label="Rack name prefix"
            hint={
              <>
                Cell row + column after this prefix is the rack name: <span className="font-mono">{plan.rack_prefix}{plan.rows[0] ?? "A"}{plan.columns[0] ?? "01"}</span>
                {suggested !== null && suggested !== plan.rack_prefix && (
                  <button className="ml-1 text-accent hover:underline" onClick={() => onChange({ ...plan, rack_prefix: suggested })}>
                    use “{suggested}” (matches your racks)
                  </button>
                )}
              </>
            }
          >
            <Input value={plan.rack_prefix} onChange={(e) => onChange({ ...plan, rack_prefix: e.target.value })} placeholder="B1_" />
          </Field>
          <Field label="Rows (top → bottom)" hint="Comma list or range like Z-R">
            <Input
              value={rowsText}
              onChange={(e) => setRowsText(e.target.value)}
              onBlur={() => {
                const rows = parseLabels(rowsText);
                if (rows.length) onChange({ ...plan, rows });
                setRowsText(rows.join(", "));
              }}
            />
          </Field>
          <Field label="Columns (left → right)" hint="Comma list or range like 01-20">
            <Input
              value={colsText}
              onChange={(e) => setColsText(e.target.value)}
              onBlur={() => {
                const columns = parseLabels(colsText);
                if (columns.length) onChange({ ...plan, columns });
                setColsText(columns.join(", "));
              }}
            />
          </Field>
          <Field label="Metric label" hint="What numbers in rack cells mean (kW, % full…)">
            <Input value={plan.metric_label ?? ""} onChange={(e) => onChange({ ...plan, metric_label: e.target.value || null })} placeholder="kW" />
          </Field>
        </div>

        {sel && (
          <div className="space-y-3 rounded-xl border border-accent/40 bg-panel-2/60 p-3">
            <div className="flex items-center justify-between">
              <p className="font-mono text-[13px] font-semibold">
                {sel.row}
                {sel.col}
              </p>
              <Button
                size="xs"
                variant="danger"
                icon={<Trash2 className="size-3.5" />}
                onClick={() => {
                  onChange({ ...plan, cells: plan.cells.filter((c) => c !== sel) });
                  setSelected(null);
                }}
              >
                Delete
              </Button>
            </div>
            <Field label="Type">
              <select
                value={sel.type}
                onChange={(e) => update(sel, { type: e.target.value as CellType })}
                className="h-8 w-full rounded-lg border border-line bg-panel-2 px-2 text-[13px]"
              >
                {CELL_TYPES.map((t) => (
                  <option key={t.type} value={t.type}>
                    {t.label}
                  </option>
                ))}
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Columns">
                <Input type="number" min={1} max={20} value={sel.span_cols} onChange={(e) => update(sel, { span_cols: Math.max(1, Number(e.target.value)) })} />
              </Field>
              <Field label="Rows">
                <Input type="number" min={1} max={20} value={sel.span_rows} onChange={(e) => update(sel, { span_rows: Math.max(1, Number(e.target.value)) })} />
              </Field>
            </div>
            <Field label="Label" hint="Overrides the row+column rack name">
              <Input value={sel.label ?? ""} onChange={(e) => update(sel, { label: e.target.value || null })} />
            </Field>
            <Field label={`Metric${plan.metric_label ? ` (${plan.metric_label})` : ""}`}>
              <Input
                type="number"
                step="0.1"
                value={sel.metric ?? ""}
                onChange={(e) => update(sel, { metric: e.target.value === "" ? null : Number(e.target.value) })}
              />
            </Field>
          </div>
        )}
      </div>
    </div>
  );
}
