import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CalendarPlus, ExternalLink, Layers, RefreshCw, Rows3, Terminal } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ActivityTable } from "./Activity";
import { StackRole } from "./Devices";
import { BookingDialog } from "../components/booking";
import { ConsolePanel } from "../components/ConsolePanel";
import { PortPanel } from "../components/PortPanel";
import { Badge, Button, Card, EmptyState, HealthBar, Property, Skeleton, StatusDot, StatusPill, TestbedPill, Textarea, clsx, useToast } from "../components/ui";
import { api } from "../lib/api";
import { relative, STATUS_LABEL, upCount } from "../lib/format";
import { inventoryOf } from "../lib/inventory";
import type { DeviceDetail, DeviceLive, DeviceSummary, StatusCounts } from "../lib/types";

type RosterMember = { serial: string; name: string | null; model: string | null; role: string | null; number: number | null };
type Roster = { key?: string; name?: string; provider?: string; members?: RosterMember[]; stack_id?: string; virtual_mac?: string; network_id?: string };

function health(devs: DeviceSummary[]): StatusCounts {
  const c: StatusCounts = { online: 0, alerting: 0, offline: 0, dormant: 0, unknown: 0 };
  devs.forEach((d) => (c[d.status] = (c[d.status] ?? 0) + 1));
  return c;
}

function portStats(live?: DeviceLive) {
  const ports = live?.ports ?? [];
  return {
    total: ports.length,
    connected: ports.filter((p) => p.status === "Connected").length,
    poe: ports.filter((p) => p.poe).length,
    errors: ports.filter((p) => (p.errors?.length ?? 0) > 0).length,
  };
}

/** One page for a whole switch stack: every member, its ports and consoles, the testbeds they are in. */
export default function StackDetail({ d }: { d: DeviceDetail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const members = d.members;
  const details = useQueries({
    queries: members.map((m) => ({ queryKey: ["device", m.id], queryFn: () => api.device(m.id), refetchInterval: 30_000 })),
  });
  const lives = useQueries({
    queries: members.map((m) => ({
      queryKey: ["device-live", m.id],
      queryFn: () => api.deviceLive(m.id),
      enabled: m.role === "switch" && !!m.provider,
      refetchInterval: 30_000,
    })),
  });
  const refresh = useMutation({
    mutationFn: () => api.refreshDevice(d.id),
    onSuccess: (s) => {
      toast({ tone: "ok", title: `${s.name}: ${STATUS_LABEL[s.status]}`, body: s.status_detail ?? undefined });
      qc.invalidateQueries({ queryKey: ["device"] });
      qc.invalidateQueries({ queryKey: ["device-live"] });
    },
    onError: (e) => toast({ tone: "bad", title: "Refresh failed", body: (e as Error).message }),
  });
  const [notes, setNotes] = useState("");
  const [booking, setBooking] = useState(false);
  useEffect(() => setNotes(d.notes ?? ""), [d.notes]);
  const saveNotes = useMutation({
    mutationFn: () => api.patchDevice(d.id, { notes }),
    onSuccess: () => {
      toast({ tone: "ok", title: "Notes saved" });
      qc.invalidateQueries({ queryKey: ["device", d.id] });
    },
  });

  const roster = ((d.live.stack ?? {}) as Roster) || {};
  const tracked = new Set(members.map((m) => m.serial?.toUpperCase()));
  const untracked = (roster.members ?? []).filter((m) => !tracked.has(m.serial?.toUpperCase()));
  const c = health(members);
  const detailOf = (i: number) => details[i]?.data;
  const metadataOf = (i: number) => inventoryOf(detailOf(i));
  const firmwares = Array.from(new Set(members.map((_, i) => metadataOf(i).firmware).filter((value): value is string => typeof value === "string")));
  const models = Array.from(new Set(members.map((m) => m.model).filter((value): value is string => typeof value === "string")));
  const externalUrl = members.map((_, i) => metadataOf(i).external_url).find(Boolean);
  const racked = members.filter((m) => m.rack);
  const consoles = members.map((m, i) => ({ m, detail: detailOf(i) })).filter((x) => x.m.has_console);

  return (
    <div className="space-y-4">
      <Link to="/devices" className="inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-ink">
        <ArrowLeft className="size-3.5" /> Devices
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl border border-accent/30 bg-accent-soft text-accent">
            <Layers className="size-5" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-mono text-xl font-semibold">{d.name}</h1>
              <StatusPill status={d.status} />
              <Badge>
                switch stack · {members.length} {members.length === 1 ? "member" : "members"}
              </Badge>
              {d.in_use_by && <Badge tone="warn">in use by {d.in_use_by}</Badge>}
              {d.removed && <Badge tone="bad">no longer reported</Badge>}
            </div>
            <p className="mt-1 text-[13px] text-muted">
              {[d.vendor, models.join(" · ") || null, d.network?.name].filter(Boolean).join(" · ")}
            </p>
            {d.status_detail && <p className="mt-1 text-[12.5px] text-faint">{d.status_detail}</p>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {externalUrl && (
            <a href={externalUrl} target="_blank" rel="noreferrer noopener">
              <Button icon={<ExternalLink className="size-4" />}>Open provider console</Button>
            </a>
          )}
          <Button icon={<RefreshCw className="size-4" />} loading={refresh.isPending} onClick={() => refresh.mutate()}>
            Refresh all members
          </Button>
          {d.testbeds.length > 0 && (
            <Button variant="primary" icon={<CalendarPlus className="size-4" />} onClick={() => setBooking(true)}>
              Book this stack
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card
            title="Members"
            subtitle={roster.provider ? `Membership reported by ${roster.provider}` : "Members declared in the testbed"}
            actions={<HealthBar health={c} className="w-24" />}
          >
            <div className="grid gap-2.5 sm:grid-cols-2 2xl:grid-cols-3">
              {members.map((m, i) => {
                const metadata = metadataOf(i);
                const ps = portStats(lives[i]?.data);
                return (
                  <Link
                    key={m.id}
                    to={`/devices/${m.id}`}
                    className={clsx(
                      "group rounded-lg border bg-panel-2 p-3 transition-colors hover:border-line-strong",
                      m.stack_role === "active" ? "border-accent/40" : "border-line",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <StatusDot status={m.status} pulse />
                      <span className="truncate font-mono text-[13px] font-semibold group-hover:text-accent">{m.name}</span>
                      <span className="flex-1" />
                      <StackRole d={m} />
                    </div>
                    <p className="mt-1 text-[12px] text-muted">{m.model ?? "—"}</p>
                    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11.5px]">
                      <dt className="text-faint">Serial</dt>
                      <dd className="truncate font-mono">{m.serial ?? "—"}</dd>
                      <dt className="text-faint">Status</dt>
                      <dd title={m.status_detail ?? undefined}>{STATUS_LABEL[m.status]}</dd>
                      {metadata.firmware && (
                        <>
                          <dt className="text-faint">Firmware</dt>
                          <dd className="truncate">{metadata.firmware}</dd>
                        </>
                      )}
                      {(metadata.lan_ip || m.mgmt_ip) && (
                        <>
                          <dt className="text-faint">IP</dt>
                          <dd className="truncate font-mono">{metadata.lan_ip || m.mgmt_ip}</dd>
                        </>
                      )}
                      {ps.total > 0 && (
                        <>
                          <dt className="text-faint">Ports</dt>
                          <dd>
                            {ps.connected}/{ps.total} connected{ps.poe ? ` · ${ps.poe} PoE` : ""}
                            {ps.errors ? <span className="text-bad"> · {ps.errors} errors</span> : null}
                          </dd>
                        </>
                      )}
                      {m.rack && (
                        <>
                          <dt className="text-faint">Rack</dt>
                          <dd className="font-mono">
                            {m.rack.name}
                            {m.rack_position ? ` U${m.rack_position}` : ""}
                          </dd>
                        </>
                      )}
                    </dl>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {m.has_console && (
                        <Badge className="py-0">
                          <Terminal className="size-3" /> console
                        </Badge>
                      )}
                      {m.in_use_by && <Badge tone="warn" className="py-0">in use by {m.in_use_by}</Badge>}
                    </div>
                  </Link>
                );
              })}
              {untracked.map((m) => (
                <div key={m.serial} className="rounded-lg border border-dashed border-line p-3 text-faint">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-mono text-[13px]">{m.name ?? m.serial}</span>
                    <span className="flex-1" />
                    <StackRole d={{ stack_role: m.role, stack_number: m.number }} />
                  </div>
                  <p className="mt-1 text-[12px]">{m.model ?? "—"}</p>
                  <p className="mt-2 font-mono text-[11.5px]">{m.serial}</p>
                  <p className="mt-1 text-[11px]">In the stack, not in a tracked testbed</p>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Ports" subtitle="Each member's faceplate, live from the status provider" bodyClassName="divide-y divide-line">
            {members.map((m, i) => {
              const live = lives[i];
              const ps = portStats(live?.data);
              return (
                <div key={m.id} className="p-4">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <StatusDot status={m.status} />
                    <Link to={`/devices/${m.id}`} className="font-mono text-[12.5px] font-semibold hover:text-accent">
                      {m.name}
                    </Link>
                    <StackRole d={m} />
                    <span className="text-[12px] text-muted">{m.model}</span>
                    <span className="flex-1" />
                    {ps.total > 0 && (
                      <span className="text-[11.5px] text-faint">
                        {ps.connected}/{ps.total} connected{ps.poe ? ` · ${ps.poe} PoE` : ""}
                      </span>
                    )}
                  </div>
                  {m.role !== "switch" || !m.provider ? (
                    <p className="text-[12.5px] text-muted">No live port source for this member.</p>
                  ) : live?.isLoading ? (
                    <Skeleton className="h-20" />
                  ) : live?.data?.error ? (
                    <p className="text-[12.5px] text-bad">{live.data.error}</p>
                  ) : live?.data?.ports.length ? (
                    <PortPanel ports={live.data.ports} legend={i === members.length - 1} />
                  ) : (
                    <p className="text-[12.5px] text-muted">No port data yet.</p>
                  )}
                </div>
              );
            })}
          </Card>

          <Card title="Consoles" subtitle={consoles.length ? `${consoles.length} of ${members.length} members have a console line` : undefined} bodyClassName="divide-y divide-line">
            {consoles.length === 0 ? (
              <p className="p-4 text-[13px] text-muted">No member has a console connection in the testbed files.</p>
            ) : (
              consoles.map(({ m, detail }) => (
                <div key={m.id} className="p-4">
                  <p className="mb-2 flex items-center gap-2 font-mono text-[12.5px] font-semibold">
                    <StatusDot status={m.status} /> {m.name} <StackRole d={m} />
                  </p>
                  {detail ? <ConsolePanel device={detail} /> : <Skeleton className="h-12" />}
                </div>
              ))
            )}
          </Card>

          <Card
            title="Testbeds"
            subtitle={`${d.testbeds.length} testbed${d.testbeds.length === 1 ? "" : "s"} use members of this stack`}
            bodyClassName=""
          >
            {d.testbeds.length === 0 ? (
              <EmptyState title="No member is part of a testbed" />
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
                      {p.device ? `${p.device} ` : ""}
                      {p.interface} → {p.tag}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </Card>

          <Card title="History" subtitle="The stack and all its members" bodyClassName="">
            <ActivityTable events={d.events} compact />
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Overview">
            <dl>
              <Property label="Status">
                <span className="flex items-center justify-end gap-1.5">
                  <StatusDot status={d.status} /> {STATUS_LABEL[d.status]}
                </span>
              </Property>
              <Property label="Members up">
                {upCount(c)} of {members.length}
                {c.alerting ? ` (${c.alerting} alerting)` : ""}
              </Property>
              <Property label="Models">{models.join(", ") || "—"}</Property>
              <Property label="Firmware">{firmwares.length > 1 ? `${firmwares.length} versions` : (firmwares[0] ?? "—")}</Property>
              <Property label="Network">
                {d.network ? (
                  <Link to={`/devices?network=${d.network.id}`} className="hover:text-accent">
                    {d.network.name}
                  </Link>
                ) : (
                  "—"
                )}
              </Property>
              {roster.stack_id && <Property label="Stack ID" mono>{roster.stack_id}</Property>}
              {roster.virtual_mac && <Property label="Virtual MAC" mono>{roster.virtual_mac}</Property>}
              <Property label="Source">{roster.provider ?? (d.source_file ? "testbed file" : (d.provider ?? "—"))}</Property>
              <Property label="Checked">{relative(members.map((_, i) => detailOf(i)?.status_checked_at).filter(Boolean).sort().pop() ?? null)}</Property>
            </dl>
          </Card>
          <Card title="Location">
            {racked.length === 0 ? (
              <p className="text-[13px] text-muted">No member is racked yet.</p>
            ) : (
              <ul className="space-y-1.5">
                {racked.map((m) => (
                  <li key={m.id} className="flex items-center gap-2 text-[12.5px]">
                    <StatusDot status={m.status} />
                    <span className="font-mono">{m.name}</span>
                    <span className="flex-1" />
                    <Link to={`/racks/${m.rack!.id}`} className="flex items-center gap-1 font-mono text-[11.5px] text-accent hover:underline">
                      <Rows3 className="size-3.5" />
                      {m.rack!.name}
                      {m.rack_position ? ` U${m.rack_position}` : ""}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Notes">
            <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Stack cabling, uplinks, known issues…" />
            <div className="mt-2 flex justify-end">
              <Button size="xs" disabled={notes === (d.notes ?? "")} loading={saveNotes.isPending} onClick={() => saveNotes.mutate()}>
                Save
              </Button>
            </div>
          </Card>
        </div>
      </div>
      {d.testbeds.length > 0 && (
        <BookingDialog
          testbed={d.testbeds[0]}
          testbeds={d.testbeds}
          open={booking}
          onClose={() => setBooking(false)}
          initialDevices={[d.id]}
        />
      )}
    </div>
  );
}
