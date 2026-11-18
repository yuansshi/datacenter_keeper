import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { GripVertical, PackageOpen, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { roleLabel, STATUS_COLOR } from "../lib/format";
import type { DeviceSummary, RackDetail } from "../lib/types";
import { Badge, EmptyState, Input, Skeleton, StatusDot, clsx, useToast } from "./ui";

const U_PX = 19;

const ROLE_TINT: Record<string, string> = {
  switch: "var(--accent)",
  router: "var(--info)",
  security_appliance: "var(--info)",
  server: "var(--violet)",
  jump_host: "var(--violet)",
  traffic_generator: "var(--warn)",
  access_point: "var(--ok)",
  camera: "var(--ok)",
};

type Over = { rackId: number; u: number } | { unrack: true } | null;

function blockHeight(d: DeviceSummary) {
  return Math.max(d.u_height || 1, 1);
}

function DeviceBlock({ d, overlay, onOpen }: { d: DeviceSummary; overlay?: boolean; onOpen?: () => void }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: `dev:${d.id}`, data: { device: d }, disabled: overlay });
  const h = blockHeight(d);
  const tint = ROLE_TINT[d.role] ?? "var(--muted)";
  return (
    <div
      ref={overlay ? undefined : setNodeRef}
      {...(overlay ? {} : listeners)}
      {...(overlay ? {} : attributes)}
      onClick={onOpen}
      title={`${d.name} · ${d.model ?? roleLabel(d.role)} · ${d.status}`}
      className={clsx(
        "group flex cursor-grab items-center gap-1.5 overflow-hidden rounded-[4px] border px-1.5 text-[11px] active:cursor-grabbing",
        isDragging && !overlay && "opacity-30",
        overlay && "shadow-2xl ring-2 ring-accent",
      )}
      style={{
        height: h * U_PX - 2,
        background: `color-mix(in srgb, ${tint} 16%, var(--panel-2))`,
        borderColor: `color-mix(in srgb, ${tint} 45%, transparent)`,
      }}
    >
      <span className="h-3/5 w-[3px] shrink-0 rounded-full" style={{ background: STATUS_COLOR[d.status] }} />
      <span className="truncate font-mono font-semibold text-ink">{d.name}</span>
      {d.model && <span className="hidden truncate text-faint sm:inline">{d.model}</span>}
      {d.in_use_by && <span className="ml-auto size-1.5 shrink-0 rounded-full bg-warn" title={`in use by ${d.in_use_by}`} />}
    </div>
  );
}

function Slot({ rackId, u, children, state }: { rackId: number; u: number; children?: React.ReactNode; state?: "ok" | "bad" | null }) {
  const { setNodeRef } = useDroppable({ id: `slot:${rackId}:${u}`, data: { rackId, u } });
  return (
    <div
      ref={setNodeRef}
      className={clsx("relative border-b border-line/50", state === "ok" && "bg-ok/20", state === "bad" && "bg-bad/20")}
      style={{ height: U_PX }}
    >
      {children}
    </div>
  );
}

function RackColumn({ rack, over, active, onOpen, compact }: { rack: RackDetail; over: Over; active: DeviceSummary | null; onOpen: (d: DeviceSummary) => void; compact?: boolean }) {
  const placed = rack.devices.filter((d) => d.rack_position && !d.removed);
  const loose = rack.devices.filter((d) => !d.rack_position && !d.is_virtual && !d.removed);
  const slots = Array.from({ length: rack.height }, (_, i) => rack.height - i);

  // preview of where the dragged device would land
  let preview: { from: number; to: number; ok: boolean } | null = null;
  if (active && over && "rackId" in over && over.rackId === rack.id) {
    const h = blockHeight(active);
    const from = Math.max(1, over.u - h + 1);
    const to = from + h - 1;
    const fits = to <= rack.height;
    const clash = placed.some((o) => o.id !== active.id && o.rack_position! <= to && from <= o.rack_position! + blockHeight(o) - 1);
    preview = { from, to, ok: fits && !clash };
  }

  return (
    <div className={clsx("shrink-0", compact ? "w-[260px]" : "w-[300px]")}>
      <div className="mb-2 flex items-end justify-between">
        <div>
          <Link to={`/racks/${rack.id}`} className="font-mono text-[14px] font-semibold hover:text-accent">
            {rack.name}
          </Link>
          <p className="text-[11px] text-faint">
            {rack.height}U · {rack.used_units}U used · {rack.device_count} devices
            {rack.floor_row ? ` · floor ${rack.floor_row}${rack.floor_col}` : ""}
          </p>
        </div>
        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-panel-3" title={`${Math.round((rack.used_units / rack.height) * 100)}% full`}>
          <div className="h-full bg-accent" style={{ width: `${(rack.used_units / rack.height) * 100}%` }} />
        </div>
      </div>
      {loose.length > 0 && (
        <div className="mb-2 rounded-lg border border-dashed border-line-strong bg-panel-2/60 p-2">
          <p className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-faint">In this rack, no U yet</p>
          <div className="space-y-1">
            {loose.map((d) => (
              <DeviceBlock key={d.id} d={d} onOpen={() => onOpen(d)} />
            ))}
          </div>
        </div>
      )}
      <div className="rounded-xl border-2 border-line-strong bg-panel-3/40 p-1 shadow-inner">
        <div className="flex">
          <div className="w-7 shrink-0 select-none">
            {slots.map((u) => (
              <div key={u} className="num flex items-center justify-end pr-1.5 text-[9.5px] text-faint" style={{ height: U_PX }}>
                {u}
              </div>
            ))}
          </div>
          <div className="relative flex-1 rounded-md bg-panel">
            {slots.map((u) => (
              <Slot key={u} rackId={rack.id} u={u} state={preview && u >= preview.from && u <= preview.to ? (preview.ok ? "ok" : "bad") : null} />
            ))}
            {placed.map((d) => {
              const top = (rack.height - (d.rack_position! + blockHeight(d) - 1)) * U_PX;
              return (
                <div key={d.id} className="absolute inset-x-0.5" style={{ top: top + 1 }}>
                  <DeviceBlock d={d} onOpen={() => onOpen(d)} />
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function UnrackedPanel({ devices, over }: { devices: DeviceSummary[]; over: Over }) {
  const { setNodeRef } = useDroppable({ id: "unrack" });
  const [q, setQ] = useState("");
  const list = devices.filter((d) => !q || [d.name, d.model, d.serial].some((v) => v?.toLowerCase().includes(q.toLowerCase())));
  const isOver = over && "unrack" in over;
  return (
    <div ref={setNodeRef} className={clsx("flex min-h-0 flex-col rounded-xl border bg-panel transition-colors", isOver ? "border-accent bg-accent-soft/30" : "border-line")}>
      <div className="border-b border-line p-3">
        <p className="text-[13px] font-semibold">Unracked devices</p>
        <p className="mb-2 text-[11.5px] text-faint">Drag into a rack slot. Drop a racked device here to take it out.</p>
        <Input icon={<Search className="size-3.5" />} placeholder="Filter" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
        {list.length === 0 ? (
          <EmptyState icon={<PackageOpen className="size-5" />} title={devices.length ? "No matches" : "Everything is racked"} />
        ) : (
          list.map((d) => <UnrackedItem key={d.id} d={d} />)
        )}
      </div>
    </div>
  );
}

function UnrackedItem({ d }: { d: DeviceSummary }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: `dev:${d.id}`, data: { device: d } });
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      className={clsx(
        "flex cursor-grab items-center gap-2 rounded-lg border border-line bg-panel-2 px-2 py-1.5 active:cursor-grabbing",
        isDragging && "opacity-40",
      )}
    >
      <GripVertical className="size-3.5 shrink-0 text-faint" />
      <StatusDot status={d.status} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-mono text-[12px] font-medium">{d.name}</p>
        <p className="truncate text-[11px] text-faint">
          {d.model ?? roleLabel(d.role)}
          {d.rack ? ` · in ${d.rack.name}` : ""}
          {d.u_height > 1 ? ` · ${d.u_height}U` : ""}
        </p>
      </div>
    </div>
  );
}

/** Rack elevations with drag & drop. ``rackIds`` are shown side by side next to the unracked list. */
export function RackBoard({ rackIds, compact }: { rackIds: number[]; compact?: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  const [active, setActive] = useState<DeviceSummary | null>(null);
  const [over, setOver] = useState<Over>(null);

  const rackQueries = useQueries({
    queries: rackIds.map((id) => ({ queryKey: ["rack", id], queryFn: () => api.rack(id), refetchInterval: 30_000 })),
  });
  const { data: unracked } = useQuery({
    queryKey: ["devices", "unracked"],
    queryFn: () => api.devices({ unracked: true }),
    refetchInterval: 30_000,
  });
  const racks = rackQueries.map((q) => q.data).filter(Boolean) as RackDetail[];
  const loading = rackQueries.some((q) => q.isLoading);

  const place = useMutation({
    mutationFn: (v: { device: DeviceSummary; rackId: number | null; position: number | null }) =>
      api.placeDevice(v.device.id, { rack_id: v.rackId, position: v.position }),
    onMutate: async (v) => {
      // optimistic: move the block locally
      const touched = new Set<number>([...(v.rackId ? [v.rackId] : []), ...(v.device.rack ? [v.device.rack.id] : [])]);
      const snapshots = new Map<number, RackDetail | undefined>();
      for (const id of touched) {
        await qc.cancelQueries({ queryKey: ["rack", id] });
        const cur = qc.getQueryData<RackDetail>(["rack", id]);
        snapshots.set(id, cur);
        if (!cur) continue;
        let devs = cur.devices.filter((d) => d.id !== v.device.id);
        if (id === v.rackId) {
          const target = racks.find((r) => r.id === id);
          devs = [...devs, { ...v.device, rack: { id, name: target?.name ?? cur.name }, rack_position: v.position }];
        }
        qc.setQueryData<RackDetail>(["rack", id], { ...cur, devices: devs });
      }
      return { snapshots };
    },
    onError: (err, _v, ctx) => {
      ctx?.snapshots.forEach((snap, id) => qc.setQueryData(["rack", id], snap));
      toast({ tone: "bad", title: "Could not move device", body: (err as Error).message });
    },
    onSuccess: (d, v) => {
      toast({ tone: "ok", title: v.rackId ? `${d.name} → ${d.rack?.name} U${d.rack_position}` : `${d.name} removed from rack` });
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["rack"] });
      qc.invalidateQueries({ queryKey: ["racks"] });
      qc.invalidateQueries({ queryKey: ["devices"] });
      qc.invalidateQueries({ queryKey: ["floorplan"] });
    },
  });

  const onDragStart = (e: DragStartEvent) => setActive((e.active.data.current as { device: DeviceSummary }).device);
  const onDragOver = (e: DragOverEvent) => {
    const id = String(e.over?.id ?? "");
    if (id.startsWith("slot:")) {
      const [, r, u] = id.split(":");
      setOver({ rackId: Number(r), u: Number(u) });
    } else if (id === "unrack") setOver({ unrack: true });
    else setOver(null);
  };
  const onDragEnd = (e: DragEndEvent) => {
    const device = active;
    setActive(null);
    setOver(null);
    if (!device || !e.over) return;
    const id = String(e.over.id);
    if (id === "unrack") {
      if (device.rack) place.mutate({ device, rackId: null, position: null });
      return;
    }
    if (id.startsWith("slot:")) {
      const [, r, u] = id.split(":");
      const h = blockHeight(device);
      const position = Math.max(1, Number(u) - h + 1);
      if (device.rack?.id === Number(r) && device.rack_position === position) return;
      const target = racks.find((x) => x.id === Number(r));
      if (target) {
        const top = position + h - 1;
        const clash = target.devices.find(
          (o) => o.id !== device.id && o.rack_position && !o.removed && o.rack_position <= top && position <= o.rack_position + blockHeight(o) - 1,
        );
        if (top > target.height) {
          toast({ tone: "bad", title: `${device.name} does not fit`, body: `${h}U at U${position} goes past the top of ${target.name} (${target.height}U)` });
          return;
        }
        if (clash) {
          toast({ tone: "bad", title: "That slot is taken", body: `U${position}${h > 1 ? `-U${top}` : ""} overlaps ${clash.name}` });
          return;
        }
      }
      place.mutate({ device, rackId: Number(r), position });
    }
  };

  const unrackedList = useMemo(() => (unracked ?? []).filter((d) => !d.is_virtual && !d.removed), [unracked]);

  return (
    <DndContext sensors={sensors} collisionDetection={pointerWithin} onDragStart={onDragStart} onDragOver={onDragOver} onDragEnd={onDragEnd} onDragCancel={() => setActive(null)}>
      <div className={clsx("flex gap-4", compact ? "flex-col" : "flex-col lg:flex-row")}>
        <div className="min-w-0 flex-1 overflow-x-auto pb-2">
          {loading && !racks.length ? (
            <Skeleton className="h-[600px] w-[300px]" />
          ) : racks.length === 0 ? (
            <EmptyState title="Pick a rack" />
          ) : (
            <div className="flex gap-6">
              {racks.map((r) => (
                <RackColumn key={r.id} rack={r} over={over} active={active} compact={compact} onOpen={(d) => navigate(`/devices/${d.id}`)} />
              ))}
            </div>
          )}
        </div>
        <div className={clsx(compact ? "h-[320px]" : "h-[640px] lg:w-[280px]", "shrink-0")}>
          <UnrackedPanel devices={unrackedList} over={over} />
        </div>
      </div>
      <DragOverlay dropAnimation={null}>{active ? <div className="w-[260px]"><DeviceBlock d={active} overlay /></div> : null}</DragOverlay>
      {place.isPending && (
        <div className="pointer-events-none fixed bottom-4 right-4 z-50">
          <Badge tone="info">Saving…</Badge>
        </div>
      )}
    </DndContext>
  );
}
