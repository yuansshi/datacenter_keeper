import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Box, Eye, Grid2x2, Image as ImageIcon, Link2Off, Pencil, Plus, RefreshCw, Rows3, Tag, Trash2, TriangleAlert } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { FloorGrid } from "../components/FloorGrid";
import { FloorPlanEditor } from "../components/FloorPlanEditor";
import { RackBoard } from "../components/RackBoard";
import { Badge, Button, Drawer, EmptyState, HealthBar, Modal, PageHeader, Segmented, Select, Skeleton, Spinner, useToast } from "../components/ui";
import { api } from "../lib/api";
import { cellKey, legendFor, linkMap, MODE_LABEL, type ColorMode, type RackLink } from "../lib/floor";
import type { FloorCell, FloorPlanInput } from "../lib/types";
import { relative } from "../lib/format";
import { readPref, writePref } from "../lib/user";

const Floor3D = lazy(() => import("../components/Floor3D").then((m) => ({ default: m.Floor3D })));

export default function FloorPlanView() {
  const id = Number(useParams().id);
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const { data: plan, isLoading } = useQuery({ queryKey: ["floorplan", id], queryFn: () => api.floorPlan(id), refetchInterval: 20_000 });
  const { data: racks } = useQuery({ queryKey: ["racks"], queryFn: api.racks });
  const [view, setView] = useState<"3d" | "top" | "2d">(() => readPref("floor.view", "3d"));
  const [mode, setMode] = useState<ColorMode>(() => readPref("floor.mode", "health"));
  const [labels, setLabels] = useState(false);
  const [selected, setSelected] = useState<{ cell: FloorCell; link?: RackLink } | null>(null);
  const [editing, setEditing] = useState<FloorPlanInput | null>(null);
  const [showImage, setShowImage] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => writePref("floor.view", view), [view]);
  useEffect(() => writePref("floor.mode", mode), [mode]);
  useEffect(() => {
    if (plan?.metric_label && mode === "health" && !plan.racks.some((r) => r.rack)) setMode("metric");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan?.id]);

  const links = useMemo(() => (plan ? linkMap(plan) : new Map<string, RackLink>()), [plan]);
  const selectedLink = selected ? links.get(cellKey(selected.cell.row, selected.cell.col)) : undefined;

  const createRack = useMutation({
    mutationFn: (cell: FloorCell) => api.createRackForCell(id, cell.row, cell.col),
    onSuccess: (r) => {
      toast({ tone: "ok", title: `Rack ${r.name} created`, body: "Drag devices into it." });
      qc.invalidateQueries({ queryKey: ["floorplan", id] });
      qc.invalidateQueries({ queryKey: ["racks"] });
    },
    onError: (e) => toast({ tone: "bad", title: "Could not create rack", body: (e as Error).message }),
  });
  const saveEdit = useMutation({
    mutationFn: (p: FloorPlanInput) => api.updateFloorPlan(id, p),
    onSuccess: () => {
      toast({ tone: "ok", title: "Floor plan saved" });
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["floorplan", id] });
      qc.invalidateQueries({ queryKey: ["floorplans"] });
    },
    onError: (e) => toast({ tone: "bad", title: "Could not save", body: (e as Error).message }),
  });
  const { data: sources } = useQuery({ queryKey: ["floorsources"], queryFn: api.floorSources, staleTime: 60_000 });
  const syncNow = useMutation({
    mutationFn: () => api.syncFloorPlan(id),
    onSuccess: (r) => {
      toast({ tone: "ok", title: `Synced ${r.name}`, body: `${r.changes} cell change${r.changes === 1 ? "" : "s"}` });
      qc.invalidateQueries({ queryKey: ["floorplan", id] });
    },
    onError: (e) => {
      toast({ tone: "bad", title: "Sync failed", body: (e as Error).message });
      qc.invalidateQueries({ queryKey: ["floorplan", id] });
    },
  });
  const detach = useMutation({
    mutationFn: () => api.detachFloorPlan(id),
    onSuccess: () => {
      toast({ tone: "ok", title: "Stopped syncing", body: "The layout is yours to edit now." });
      qc.invalidateQueries({ queryKey: ["floorplan", id] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteFloorPlan(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["floorplans"] });
      navigate("/floor");
    },
  });

  if (isLoading || !plan) return <Skeleton className="h-[640px]" />;
  const sourceName = plan.source_ref?.split(":")[0] ?? null;
  const source = sources?.find((s) => s.name === sourceName);
  const sourceLabel = source?.label ?? sourceName?.toUpperCase();

  const rackCells = plan.cells.filter((c) => c.type === "rack" || c.type === "empty_rack");
  const linked = plan.racks.filter((r) => r.rack);
  const devices = linked.flatMap((r) => r.devices);
  const health = {
    online: devices.filter((d) => d.status === "online").length,
    alerting: devices.filter((d) => d.status === "alerting").length,
    offline: devices.filter((d) => d.status === "offline").length,
    dormant: devices.filter((d) => d.status === "dormant").length,
    unknown: devices.filter((d) => d.status === "unknown").length,
  };

  const onSelect = (cell: FloorCell, link?: RackLink) => {
    if (cell.type === "rack" || cell.type === "empty_rack") setSelected({ cell, link });
  };

  if (editing) {
    return (
      <div>
        <PageHeader
          title={`Edit ${plan.name}`}
          icon={<Pencil className="size-5" />}
          actions={
            <>
              <Button variant="ghost" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button variant="primary" loading={saveEdit.isPending} onClick={() => saveEdit.mutate(editing)}>
                Save changes
              </Button>
            </>
          }
        />
        {plan.source_ref && (
          <p className="mb-3 flex items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[12.5px]">
            <TriangleAlert className="size-4 text-warn" />
            Cells come from {sourceLabel} and are replaced on the next sync. Name, site and rack prefix are kept. Use “Stop syncing” to edit the
            layout freely.
          </p>
        )}
        <div className="rounded-xl border border-line bg-panel p-4">
          <FloorPlanEditor plan={editing} onChange={setEditing} rackNames={(racks ?? []).map((r) => r.name)} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <Link to="/floor" className="inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-ink">
        <ArrowLeft className="size-3.5" /> Floor plans
      </Link>
      <PageHeader
        title={plan.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            {plan.site && <span>{plan.site}</span>}
            <span>
              {plan.rows.length}×{plan.columns.length} grid · {rackCells.length} rack positions · {linked.length} linked · {devices.length} devices
            </span>
            <HealthBar health={health} className="w-24" />
            {plan.source_ref && (
              <Badge tone={plan.sync_error ? "bad" : "accent"} title={plan.sync_error ?? plan.source_ref}>
                {sourceLabel}
                {plan.source_extra?.lab_name ? ` · ${String(plan.source_extra.lab_name)}` : ""} · {plan.sync_error ? "sync failed" : `synced ${relative(plan.synced_at)}`}
              </Badge>
            )}
          </span>
        }
        icon={<Box className="size-5" />}
        actions={
          <>
            {plan.source_ref && source?.can_sync && (
              <Button icon={<RefreshCw className="size-4" />} loading={syncNow.isPending} onClick={() => syncNow.mutate()}>
                Sync now
              </Button>
            )}
            {plan.source_ref && !source?.can_sync && (
              <Link to="/floor" title={source?.sync_disabled_reason ?? undefined}>
                <Button icon={<RefreshCw className="size-4" />}>Update from export</Button>
              </Link>
            )}
            {plan.source_ref && (
              <Button variant="ghost" icon={<Link2Off className="size-4" />} loading={detach.isPending} onClick={() => detach.mutate()} title="Stop syncing this plan">
                Stop syncing
              </Button>
            )}
            {plan.has_image && (
              <Button icon={<ImageIcon className="size-4" />} onClick={() => setShowImage(true)}>
                Source image
              </Button>
            )}
            <Button
              icon={<Pencil className="size-4" />}
              onClick={() =>
                setEditing({
                  name: plan.name,
                  site: plan.site,
                  rack_prefix: plan.rack_prefix,
                  rows: plan.rows,
                  columns: plan.columns,
                  cells: plan.cells,
                  metric_label: plan.metric_label,
                  notes: plan.notes,
                })
              }
            >
              Edit layout
            </Button>
            <Button variant="ghost" icon={<Trash2 className="size-4" />} onClick={() => setConfirmDelete(true)} aria-label="Delete floor plan" />
          </>
        }
      />

      {plan.sync_error && (
        <p className="flex items-center gap-2 rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-[12.5px]">
          <TriangleAlert className="size-4 text-bad" /> Last sync from {sourceLabel} failed: {plan.sync_error}. Showing the layout from {relative(plan.synced_at)}.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Segmented
          value={view}
          onChange={setView}
          options={[
            { value: "3d", label: <span className="flex items-center gap-1"><Box className="size-3.5" />3D</span> },
            { value: "top", label: <span className="flex items-center gap-1"><Eye className="size-3.5" />Top</span> },
            { value: "2d", label: <span className="flex items-center gap-1"><Grid2x2 className="size-3.5" />Map</span> },
          ]}
        />
        <Select value={mode} onChange={(e) => setMode(e.target.value as ColorMode)} aria-label="Colour racks by">
          {(Object.keys(MODE_LABEL) as ColorMode[])
            .filter((m) => m !== "metric" || plan.cells.some((c) => c.metric != null))
            .map((m) => (
              <option key={m} value={m}>
                Colour: {m === "metric" && plan.metric_label ? plan.metric_label : MODE_LABEL[m]}
              </option>
            ))}
        </Select>
        {view !== "2d" && (
          <Button size="sm" variant={labels ? "primary" : "secondary"} icon={<Tag className="size-3.5" />} onClick={() => setLabels((v) => !v)}>
            Rack names
          </Button>
        )}
        <div className="flex flex-wrap items-center gap-3 text-[11.5px] text-muted">
          {legendFor(mode, plan.metric_label).map((l) => (
            <span key={l.label} className="flex items-center gap-1.5">
              <span className="size-2.5 rounded-sm" style={{ background: l.color }} />
              {l.label}
            </span>
          ))}
        </div>
      </div>

      <div className="relative overflow-hidden rounded-xl border border-line bg-panel shadow-panel" style={{ height: "min(72vh, 760px)" }}>
        {view === "2d" ? (
          <div className="h-full overflow-auto p-4">
            <FloorGrid
              plan={plan}
              links={links}
              mode={mode}
              selected={selected ? cellKey(selected.cell.row, selected.cell.col) : null}
              onCellClick={(c) => onSelect(c, links.get(cellKey(c.row, c.col)))}
              cellSize={Math.max(26, Math.min(44, Math.floor(1100 / Math.max(plan.columns.length, 1))))}
            />
          </div>
        ) : (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center gap-2 text-muted">
                <Spinner /> Loading 3D view…
              </div>
            }
          >
            <Floor3D
              plan={plan}
              mode={mode}
              view={view === "top" ? "top" : "perspective"}
              showLabels={labels}
              selected={selected ? cellKey(selected.cell.row, selected.cell.col) : null}
              onSelect={onSelect}
            />
          </Suspense>
        )}
        <p className="pointer-events-none absolute bottom-2 left-3 text-[11px] text-faint">
          {view === "2d" ? "Click a rack to open it" : "Drag to orbit · scroll to zoom · right-drag to pan · click a rack to open it"}
        </p>
      </div>

      <Drawer
        open={!!selected}
        onClose={() => setSelected(null)}
        width={selectedLink?.rack ? 640 : 420}
        title={selectedLink?.rack_name ?? (selected ? `${selected.cell.row}${selected.cell.col}` : "")}
        subtitle={
          selected
            ? `Cell ${selected.cell.row}${selected.cell.col}${selected.cell.metric != null ? ` · ${selected.cell.metric} ${plan.metric_label ?? ""}` : ""}${selected.cell.type === "empty_rack" ? " · empty rack" : ""}`
            : undefined
        }
        actions={
          selectedLink?.rack ? (
            <Link to={`/racks/${selectedLink.rack.id}`}>
              <Button size="xs" icon={<Rows3 className="size-3.5" />}>
                Rack page
              </Button>
            </Link>
          ) : null
        }
      >
        {selected && selectedLink?.rack ? (
          <div className="p-4">
            <div className="mb-3 flex flex-wrap gap-2">
              <Badge>{selectedLink.rack.height}U</Badge>
              <Badge tone="accent">{selectedLink.rack.used_units}U used</Badge>
              <Badge>{selectedLink.devices.length} devices</Badge>
              {selectedLink.rack.in_use > 0 && <Badge tone="warn">{selectedLink.rack.in_use} in a booked testbed</Badge>}
              {plan.source_extra?.asset_counts?.[`${selected.cell.row}${selected.cell.col}`] !== undefined && (
                <Badge tone="info" title={`Asset count reported by ${sourceLabel}`}>
                  {sourceLabel}: {plan.source_extra.asset_counts[`${selected.cell.row}${selected.cell.col}`]} assets
                </Badge>
              )}
            </div>
            <RackBoard rackIds={[selectedLink.rack.id]} compact />
          </div>
        ) : selected ? (
          <EmptyState
            icon={<Rows3 className="size-5" />}
            title={`No rack named ${selectedLink?.rack_name ?? ""} yet`}
            action={
              <Button variant="primary" icon={<Plus className="size-4" />} loading={createRack.isPending} onClick={() => createRack.mutate(selected.cell)}>
                Create rack {selectedLink?.rack_name}
              </Button>
            }
          >
            Create it to start placing devices here. Devices whose testbed file says{" "}
            <span className="font-mono">location: {selectedLink?.rack_name}_U05</span> will land in it automatically.
          </EmptyState>
        ) : null}
      </Drawer>

      <Modal open={showImage} onClose={() => setShowImage(false)} title="Source image" width={1100}>
        <img src={`/api/v1/floorplans/${id}/image`} alt="Floor map source" className="w-full rounded-lg" />
      </Modal>
      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete this floor plan?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Keep
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
              Delete floor plan
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-muted">Racks and devices stay; they are only unlinked from this room.</p>
      </Modal>
    </div>
  );
}
