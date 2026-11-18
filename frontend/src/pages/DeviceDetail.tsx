import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, CalendarPlus, ExternalLink, Layers, RefreshCw, Rows3, Server } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ActivityTable } from "./Activity";
import { StackRole } from "./Devices";
import StackDetail from "./StackDetail";
import { BookingDialog } from "../components/booking";
import { ConsolePanel } from "../components/ConsolePanel";
import { PortPanel } from "../components/PortPanel";
import { Badge, Button, Card, EmptyState, Property, Skeleton, StatusDot, StatusPill, TestbedPill, Textarea, clsx, useToast } from "../components/ui";
import { api } from "../lib/api";
import { relative, roleLabel, stamp } from "../lib/format";
import { inventoryOf } from "../lib/inventory";

function MiniRack({ height, position, uHeight, name }: { height: number; position: number; uHeight: number; name: string }) {
  const slots = Array.from({ length: height }, (_, i) => height - i);
  return (
    <div className="flex items-center gap-3">
      <div className="flex w-10 flex-col gap-px rounded border border-line bg-panel-3 p-0.5">
        {slots.map((u) => (
          <div
            key={u}
            className="h-[3px] rounded-[1px]"
            style={{ background: u >= position && u < position + Math.max(uHeight, 1) ? "var(--accent)" : "var(--line)" }}
          />
        ))}
      </div>
      <div className="text-[12.5px]">
        <p className="font-mono font-medium">{name}</p>
        <p className="text-muted">
          U{position}
          {uHeight > 1 ? `–U${position + uHeight - 1}` : ""} of {height}U
        </p>
      </div>
    </div>
  );
}

export default function DeviceDetail() {
  const id = Number(useParams().id);
  const qc = useQueryClient();
  const toast = useToast();
  const { data: d, isLoading } = useQuery({ queryKey: ["device", id], queryFn: () => api.device(id), refetchInterval: 20_000 });
  const live = useQuery({
    queryKey: ["device-live", id],
    queryFn: () => api.deviceLive(id),
    enabled: !!d && d.role === "switch" && !d.is_virtual && !!d.provider,
    refetchInterval: 30_000,
  });
  const { data: rack } = useQuery({ queryKey: ["rack", d?.rack?.id], queryFn: () => api.rack(d!.rack!.id), enabled: !!d?.rack });
  const { data: stack } = useQuery({
    queryKey: ["device", d?.parent?.id],
    queryFn: () => api.device(d!.parent!.id),
    enabled: !!d?.parent,
    refetchInterval: 30_000,
  });
  const refresh = useMutation({
    mutationFn: () => api.refreshDevice(id),
    onSuccess: (s) => {
      toast({ tone: "ok", title: `${s.name} is ${s.status}` });
      qc.invalidateQueries({ queryKey: ["device", id] });
      qc.invalidateQueries({ queryKey: ["device-live", id] });
    },
    onError: (e) => toast({ tone: "bad", title: "Refresh failed", body: (e as Error).message }),
  });
  const [notes, setNotes] = useState("");
  const [booking, setBooking] = useState(false);
  useEffect(() => setNotes(d?.notes ?? ""), [d?.notes]);
  const saveNotes = useMutation({
    mutationFn: () => api.patchDevice(id, { notes }),
    onSuccess: () => {
      toast({ tone: "ok", title: "Notes saved" });
      qc.invalidateQueries({ queryKey: ["device", id] });
    },
  });

  if (isLoading || !d) return <Skeleton className="h-[480px]" />;
  if (d.is_virtual && d.members.length > 0) return <StackDetail d={d} />;
  const inventory = inventoryOf(d);
  const reach = d.live.reachability ?? {};
  const openPorts = Array.isArray(reach.open_ports) ? reach.open_ports.filter((value): value is number => typeof value === "number") : undefined;
  const reachCheckedAt = typeof reach.checked_at === "string" ? reach.checked_at : undefined;

  return (
    <div className="space-y-4">
      <Link to="/devices" className="inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-ink">
        <ArrowLeft className="size-3.5" /> Devices
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl border border-line bg-panel-2 text-accent">
            <Server className="size-5" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-mono text-xl font-semibold">{d.name}</h1>
              <StatusPill status={d.status} />
              {d.is_virtual && <Badge>logical stack</Badge>}
              {d.is_shared && <Badge>shared support device</Badge>}
              {d.in_use_by && <Badge tone="warn">in use by {d.in_use_by}</Badge>}
              {d.removed && <Badge tone="bad">removed from testbed files</Badge>}
            </div>
            <p className="mt-1 text-[13px] text-muted">
              {[d.vendor, d.model, roleLabel(d.role)].filter(Boolean).join(" · ")}
              {d.serial && (
                <>
                  {" · "}
                  <span className="font-mono">{d.serial}</span>
                </>
              )}
            </p>
            {d.status_detail && <p className="mt-1 text-[12.5px] text-faint">{d.status_detail}</p>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {inventory.external_url && (
            <a href={inventory.external_url} target="_blank" rel="noreferrer noopener">
              <Button icon={<ExternalLink className="size-4" />}>Open provider console</Button>
            </a>
          )}
          {d.provider && (
            <Button icon={<RefreshCw className="size-4" />} loading={refresh.isPending} onClick={() => refresh.mutate()}>
              Refresh status
            </Button>
          )}
          {d.testbeds.some((t) => t.exclusive) && (
            <Button variant="primary" icon={<CalendarPlus className="size-4" />} onClick={() => setBooking(true)}>
              Book this device
            </Button>
          )}
        </div>
      </div>

      {stack && stack.members.length > 0 && (
        <Link
          to={`/devices/${stack.id}`}
          className="group flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-accent/30 bg-accent-soft/40 px-4 py-3 hover:border-accent/60"
        >
          <span className="flex items-center gap-2">
            <Layers className="size-4 text-accent" />
            <span className="text-[12.5px] text-muted">Member of stack</span>
            <span className="font-mono text-[13px] font-semibold">{stack.name}</span>
            <StackRole d={d} />
          </span>
          <span className="flex flex-wrap items-center gap-3">
            {stack.members.map((m) => (
              <span key={m.id} className={clsx("flex items-center gap-1.5 font-mono text-[11.5px]", m.id === d.id ? "text-ink" : "text-muted")}>
                <StatusDot status={m.status} /> {m.name}
              </span>
            ))}
          </span>
          <span className="flex-1" />
          <span className="flex items-center gap-1 text-[12px] font-medium text-accent">
            Combined stack view <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
          </span>
        </Link>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          {d.role === "switch" && !d.is_virtual && d.provider && (
            <Card
              title="Ports"
              subtitle={live.data?.source ? `Live from ${live.data.source}` : undefined}
              actions={live.isFetching ? <span className="text-[11px] text-faint">updating…</span> : null}
            >
              {live.isLoading ? (
                <Skeleton className="h-24" />
              ) : live.data?.error ? (
                <p className="text-[13px] text-bad">{live.data.error}</p>
              ) : live.data?.ports.length ? (
                <PortPanel ports={live.data.ports} />
              ) : (
                <p className="text-[13px] text-muted">No port data from the status provider.</p>
              )}
            </Card>
          )}
          <Card title="Console & connections">
            <ConsolePanel device={d} />
          </Card>
          {d.members.length > 0 && (
            <Card title="Stack members" bodyClassName="divide-y divide-line">
              {d.members.map((m) => (
                <Link key={m.id} to={`/devices/${m.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-panel-2">
                  <StatusDot status={m.status} />
                  <span className="font-mono text-[12.5px] font-medium">{m.name}</span>
                  <span className="text-[12px] text-muted">{m.model}</span>
                  <span className="font-mono text-[11.5px] text-faint">{m.serial}</span>
                  <span className="flex-1" />
                  {m.rack && (
                    <span className="font-mono text-[11.5px] text-faint">
                      {m.rack.name} U{m.rack_position}
                    </span>
                  )}
                </Link>
              ))}
            </Card>
          )}
          <Card title="Testbeds" subtitle={`${d.testbeds.length} testbed${d.testbeds.length === 1 ? "" : "s"} use this device`} bodyClassName="">
            {d.testbeds.length === 0 ? (
              <EmptyState title="Not part of any testbed" />
            ) : (
              <div className="flex flex-wrap gap-2 p-4">
                {d.testbeds.map((t) => (
                  <Link
                    key={t.id}
                    to={`/testbeds/${t.id}`}
                    className="flex items-center gap-2 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 hover:border-line-strong"
                  >
                    <span className="font-mono text-[12.5px]">{t.name}</span>
                    <TestbedPill status={t.status} />
                  </Link>
                ))}
              </div>
            )}
            {d.port_tags.length > 0 && (
              <div className="border-t border-line px-4 py-3">
                <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">Topology ports</p>
                <div className="flex flex-wrap gap-1.5">
                  {d.port_tags.map((p, i) => (
                    <Badge key={i} tone="accent" className="font-mono" title={p.testbed}>
                      {p.interface} → {p.tag}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </Card>
          <Card title="History" bodyClassName="">
            <ActivityTable events={d.events} compact />
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Overview">
            <dl>
              <Property label="Status">
                <span className="flex items-center justify-end gap-1.5">
                  <StatusDot status={d.status} /> {d.status} · {relative(d.status_changed_at)}
                </span>
              </Property>
              <Property label="Last seen">{d.last_seen_at ? relative(d.last_seen_at) : "—"}</Property>
              <Property label="Checked">{d.status_checked_at ? relative(d.status_checked_at) : "never"}</Property>
              <Property label="Model">{d.model}</Property>
              <Property label="Serial" mono>{d.serial}</Property>
              <Property label="Management IP" mono>{d.mgmt_ip}</Property>
              <Property label="Type / OS" mono>{[d.type, d.os, d.platform].filter(Boolean).join(" / ") || "—"}</Property>
              <Property label="Network">
                {d.network ? (
                  <Link to={`/devices?network=${d.network.id}`} className="hover:text-accent">
                    {d.network.name}
                  </Link>
                ) : (
                  "—"
                )}
              </Property>
              {d.parent && (
                <Property label="Stack">
                  <Link to={`/devices/${d.parent.id}`} className="font-mono hover:text-accent">
                    {d.parent.name}
                  </Link>
                </Property>
              )}
              <Property label="Status source">{d.provider ?? "none"}</Property>
            </dl>
          </Card>
          {(inventory.firmware || inventory.lan_ip || inventory.mac || inventory.network_name || openPorts) && (
            <Card title="Provider inventory">
              <dl>
                {inventory.source && <Property label="Source">{inventory.source}</Property>}
                {inventory.firmware && <Property label="Firmware">{inventory.firmware}</Property>}
                {inventory.lan_ip && <Property label="Management IP" mono>{inventory.lan_ip}</Property>}
                {inventory.mac && <Property label="MAC" mono>{inventory.mac}</Property>}
                {inventory.network_name && <Property label="Network">{inventory.network_name}</Property>}
                {inventory.region && <Property label="Region">{inventory.region}</Property>}
                {inventory.product_type && <Property label="Product">{inventory.product_type}</Property>}
                {openPorts && <Property label="Open ports">{openPorts.join(", ") || "none"}</Property>}
                <Property label="Updated">{relative(inventory.checked_at ?? reachCheckedAt)}</Property>
              </dl>
            </Card>
          )}
          <Card
            title="Location"
            actions={
              d.rack ? (
                <Link to={`/racks/${d.rack.id}`} className="flex items-center gap-1 text-[12px] text-accent hover:underline">
                  <Rows3 className="size-3.5" /> Rack view
                </Link>
              ) : null
            }
          >
            {d.rack && d.rack_position && rack ? (
              <MiniRack height={rack.height} position={d.rack_position} uHeight={d.u_height} name={d.rack.name} />
            ) : d.rack ? (
              <p className="text-[13px] text-muted">
                In <span className="font-mono text-ink">{d.rack.name}</span>, no U position yet.{" "}
                <Link to={`/racks/${d.rack.id}`} className="text-accent hover:underline">
                  Place it
                </Link>
              </p>
            ) : d.is_virtual ? (
              <p className="text-[13px] text-muted">Logical device; its members are racked individually.</p>
            ) : (
              <p className="text-[13px] text-muted">
                Not racked.{" "}
                <Link to="/racks" className="text-accent hover:underline">
                  Drag it into a rack
                </Link>
              </p>
            )}
            {d.location && <p className="mt-2 font-mono text-[11.5px] text-faint">location: {d.location}</p>}
            {d.placement_source === "manual" && <p className="mt-1 text-[11.5px] text-faint">Placed by hand (YAML location ignored)</p>}
          </Card>
          <Card title="Notes">
            <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Cabling quirks, known issues…" />
            <div className="mt-2 flex justify-end">
              <Button size="xs" disabled={notes === (d.notes ?? "")} loading={saveNotes.isPending} onClick={() => saveNotes.mutate()}>
                Save
              </Button>
            </div>
          </Card>
          <Card title="Source">
            <p className="break-all font-mono text-[11px] text-muted">{d.source_file}</p>
            <p className="mt-2 text-[11px] text-faint">
              Added {stamp(d.created_at)} · updated {relative(d.updated_at)}
            </p>
          </Card>
        </div>
      </div>
      {d.testbeds.some((t) => t.exclusive) && (
        <BookingDialog
          testbed={d.testbeds.find((t) => t.exclusive)!}
          testbeds={d.testbeds.filter((t) => t.exclusive)}
          open={booking}
          onClose={() => setBooking(false)}
          initialDevices={[d.id]}
        />
      )}
    </div>
  );
}
