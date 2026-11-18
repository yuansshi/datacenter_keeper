import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CalendarPlus, FileCode2, FlaskConical, ListChecks, Wrench } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { BookingActions, BookingDialog, BookingLine } from "../components/booking";
import { ConsoleLines, TrafficGenerators } from "../components/LabAccess";
import { Timeline } from "../components/Timeline";
import { DeviceTable } from "./Devices";
import { Badge, Button, Card, EmptyState, HealthBar, Input, Modal, Skeleton, StatusDot, Tabs, TestbedPill, useToast } from "../components/ui";
import { api } from "../lib/api";
import { when } from "../lib/format";
import type { DeviceStatus, Network, TestbedDetail as TestbedData } from "../lib/types";

const HEALTH_STATES: ReadonlyArray<{ status: DeviceStatus; label: string }> = [
  { status: "online", label: "Online" },
  { status: "alerting", label: "Alerting" },
  { status: "offline", label: "Offline" },
  { status: "dormant", label: "Dormant" },
  { status: "unknown", label: "Unknown" },
];
const ATTENTION_ORDER: Record<DeviceStatus, number> = { offline: 0, dormant: 1, alerting: 2, unknown: 3, online: 4 };

export function TestbedHealthOverview({ testbed: t }: { testbed: Pick<TestbedData, "health" | "shares_with"> }) {
  return (
    <Card title="Health">
      <HealthBar health={t.health} />
      <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[12px] sm:grid-cols-5 lg:grid-cols-3">
        {HEALTH_STATES.map(({ status, label }) => (
          <div key={status} className="rounded-lg bg-panel-2 py-2">
            <p className="num text-lg font-semibold">{t.health[status]}</p>
            <p className="flex items-center justify-center gap-1.5 text-muted"><StatusDot status={status} />{label}</p>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted">Latest physical-device status from configured providers. Offline, dormant, alerting and unknown remain separate.</p>
      {t.shares_with.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">Shares hardware with</p>
          <p className="mb-2 text-xs leading-relaxed text-muted">These testbed definitions reuse physical devices, so their health can repeat. Booking shared hardware can affect availability in each testbed.</p>
          <div className="flex flex-wrap gap-1">
            {t.shares_with.slice(0, 24).map((s) => (
              <Link key={s.id} to={`/testbeds/${s.id}`} className="rounded-md bg-panel-2 px-1.5 py-0.5 font-mono text-[11px] text-muted hover:text-accent">{s.name}</Link>
            ))}
            {t.shares_with.length > 24 && <span className="text-[11px] text-faint">+{t.shares_with.length - 24}</span>}
          </div>
        </div>
      )}
    </Card>
  );
}

export function TestbedAttention({ devices, networks, networksUnavailable }: {
  devices: TestbedData["devices"];
  networks: Network[] | undefined;
  networksUnavailable: boolean;
}) {
  const attention = devices.filter((d) => d.exclusive && !d.is_virtual && !d.removed && d.status !== "online")
    .sort((a, b) => ATTENTION_ORDER[a.status] - ATTENTION_ORDER[b.status] || a.name.localeCompare(b.name));
  const observedNetworks = new Map<number, Network>(networksUnavailable ? [] : networks?.map((n) => [n.id, n]));
  return (
    <Card title={`Needs attention (${attention.length})`} subtitle="Physical devices that are offline, dormant, alerting or unknown; open a device for its latest evidence.">
      {!attention.length ? <p className="text-sm text-muted">No physical devices currently need attention.</p> : (
        <ul className="divide-y divide-line">
          {attention.map((d) => {
            const network = d.network ? observedNetworks.get(d.network.id) : undefined;
            const region = network?.region || "Not provided";
            const statusLabel = HEALTH_STATES.find((s) => s.status === d.status)?.label ?? "Unknown";
            return (
              <li key={d.id} className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:gap-5">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link to={`/devices/${d.id}`} className="break-all font-mono text-[13px] font-semibold text-accent hover:underline">{d.name}</Link>
                    <Badge tone={d.status === "offline" ? "bad" : d.status === "dormant" || d.status === "alerting" ? "warn" : "neutral"}><StatusDot status={d.status} />{statusLabel}</Badge>
                  </div>
                  <p className="mt-1 font-mono text-xs text-muted">{d.serial || "Serial not recorded"}</p>
                  <p className="mt-1 text-xs leading-relaxed text-muted">{d.status_detail || "No further status evidence is recorded."}</p>
                </div>
                <dl className="grid shrink-0 grid-cols-2 gap-x-5 gap-y-1 text-xs sm:w-64">
                  <dt className="text-faint">Region / group</dt><dd className="break-words text-muted">{region}</dd>
                  <dt className="text-faint">Observed network</dt><dd className="break-words text-muted">{network?.name || "Unknown"}</dd>
                </dl>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function SourceView({ id }: { id: number }) {
  const { data, isLoading } = useQuery({ queryKey: ["testbed-source", id], queryFn: () => api.testbedSource(id) });
  const [active, setActive] = useState(0);
  if (isLoading) return <Skeleton className="h-64" />;
  if (!data?.files.length) return <EmptyState title="Source file not available" />;
  const file = data.files[Math.min(active, data.files.length - 1)];
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1">
        {data.files.map((f, i) => (
          <button
            key={f.path}
            onClick={() => setActive(i)}
            className={`rounded-md px-2 py-1 font-mono text-[11.5px] ${i === active ? "bg-accent-soft text-accent" : "text-muted hover:bg-panel-2"}`}
          >
            {i === 0 ? f.name : `↳ ${f.name}`}
          </button>
        ))}
      </div>
      <p className="mb-2 break-all font-mono text-[11px] text-faint">{file.path}</p>
      <pre className="max-h-[560px] overflow-auto rounded-lg border border-line bg-bg p-3 font-mono text-[11.5px] leading-relaxed text-muted">{file.text}</pre>
      <p className="mt-2 text-[11px] text-faint">Passwords and secrets are masked. Edit the file in your repo; Labkeeper re-imports on save.</p>
    </div>
  );
}

export default function TestbedDetail() {
  const id = Number(useParams().id);
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<"devices" | "schedule" | "topology" | "source">("devices");
  const [bookOpen, setBookOpen] = useState(false);
  const [bookAt, setBookAt] = useState<Date | null>(null);
  const [bookDevices, setBookDevices] = useState<number[] | null>(null);
  const [maintOpen, setMaintOpen] = useState(false);
  const [note, setNote] = useState("");
  const { data: t, isLoading } = useQuery({ queryKey: ["testbed", id], queryFn: () => api.testbed(id), refetchInterval: 15_000 });
  const networks = useQuery({ queryKey: ["networks"], queryFn: api.networks, refetchInterval: 15_000 });
  const maint = useMutation({
    mutationFn: (on: boolean) => api.patchTestbed(id, { maintenance: on, maintenance_note: on ? note || null : null }),
    onSuccess: (r) => {
      toast({ tone: "ok", title: r.maintenance ? `${r.name} in maintenance` : `${r.name} back in service` });
      qc.invalidateQueries();
      setMaintOpen(false);
    },
  });
  if (isLoading || !t) return <Skeleton className="h-[480px]" />;

  const active = t.bookings.filter((b) => b.state === "active" || b.state === "upcoming").sort((a, b) => a.starts_at.localeCompare(b.starts_at));
  const topoRows = Object.entries(t.topology).flatMap(([dev, ifaces]) =>
    Object.entries(ifaces).map(([iface, spec]) => ({ dev, iface, tag: spec.tag, type: spec.type, link: spec.link })),
  );
  const dev = t.devices.filter((d) => d.exclusive);
  const support = t.devices.filter((d) => !d.exclusive);
  const all = [...t.devices, ...t.stacks];
  const openBooking = (at: Date | null, devices: number[] | null = null) => {
    setBookAt(at);
    setBookDevices(devices);
    setBookOpen(true);
  };

  return (
    <div className="space-y-4">
      <Link to="/testbeds" className="inline-flex items-center gap-1 text-[12.5px] text-muted hover:text-ink">
        <ArrowLeft className="size-3.5" /> Testbeds
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl border border-line bg-panel-2 text-accent">
            <FlaskConical className="size-5" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-mono text-xl font-semibold">{t.name}</h1>
              <TestbedPill status={t.status} />
              {t.kind && <Badge>{t.kind}</Badge>}
            </div>
            <p className="mt-1 text-[13px] text-muted">
              {t.exclusive_count} devices{t.stacks.length ? ` in ${t.stacks.length} stack${t.stacks.length === 1 ? "" : "s"} and more` : ""} ·{" "}
              {t.models.join(", ") || "no models"} · <span className="font-mono">{t.key}</span>
            </p>
            {t.busy_count > 0 && t.status !== "in_use" && (
              <p className="mt-1 text-[12.5px] text-info">
                {t.busy_count} of {t.exclusive_count} devices in use right now; the rest can be booked.
              </p>
            )}
            {t.status === "maintenance" && <p className="mt-1 text-[12.5px] text-warn">{t.maintenance_note ?? "In maintenance"}</p>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            icon={<Wrench className="size-4" />}
            onClick={() => (t.maintenance ? maint.mutate(false) : setMaintOpen(true))}
            loading={maint.isPending}
          >
            {t.maintenance ? "End maintenance" : "Maintenance"}
          </Button>
          {t.status !== "maintenance" && t.status !== "in_use" && (
            <Button icon={<ListChecks className="size-4" />} onClick={() => openBooking(null, [])}>
              Book devices
            </Button>
          )}
          {t.status !== "maintenance" && (
            <Button
              variant="primary"
              icon={<CalendarPlus className="size-4" />}
              onClick={() =>
                openBooking(
                  t.status === "free" ? null : new Date((t.current ?? t.partial[0] ?? t.blocked_by[0])?.effective_end ?? Date.now()),
                )
              }
            >
              {t.status === "free" ? "Book now" : "Schedule"}
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Bookings" className="lg:col-span-2" bodyClassName="divide-y divide-line">
          {active.length === 0 ? (
            <EmptyState title="No current or upcoming bookings" />
          ) : (
            active.map((b) => (
              <div key={b.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <BookingLine booking={b} showTestbed={false} />
                <div className="flex items-center gap-2">
                  <Badge tone={b.state === "active" ? "warn" : "info"}>{b.state === "active" ? "now" : when(b.starts_at)}</Badge>
                  <BookingActions booking={b} compact />
                </div>
              </div>
            ))
          )}
          {t.blocked_by.length > 0 && (
            <div className="bg-violet/5 px-4 py-3 text-[12.5px]">
              <p className="mb-1 font-medium text-violet">Shared hardware in use elsewhere</p>
              {t.blocked_by.map((b) => (
                <p key={b.id} className="text-muted">
                  {b.devices_label ? <span className="font-mono text-ink">{b.devices_label}</span> : "All of "}
                  {b.devices_label ? " in " : ""}
                  <Link to={`/testbeds/${b.testbed_id}`} className="font-mono text-ink hover:text-accent">
                    {b.testbed_name}
                  </Link>{" "}
                  booked by {b.owner} until {when(b.effective_end)}
                </p>
              ))}
            </div>
          )}
        </Card>
        <TestbedHealthOverview testbed={t} />
      </div>

      <TestbedAttention devices={t.devices} networks={networks.data} networksUnavailable={networks.isPending || networks.isError || networks.fetchStatus === "paused"} />

      <div>
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "devices", label: `Devices (${t.devices.length})` },
            { value: "schedule", label: "Schedule" },
            { value: "topology", label: `Topology (${topoRows.length})` },
            { value: "source", label: "Source YAML" },
          ]}
        />
        {tab === "devices" && (
          <div className="space-y-4">
            <Card
              title="Devices under test"
              subtitle="Book the whole testbed, or just a stack or a switch"
              actions={
                t.status !== "maintenance" && t.status !== "in_use" ? (
                  <Button size="xs" icon={<ListChecks className="size-3.5" />} onClick={() => openBooking(null, [])}>
                    Book devices
                  </Button>
                ) : null
              }
              bodyClassName=""
            >
              <DeviceTable devices={dev} all={all} />
            </Card>
            <ConsoleLines lines={t.consoles} total={t.devices.filter((d) => !(d.is_virtual && d.member_count > 0)).length} />
            <TrafficGenerators generators={t.traffic} />
            {support.length > 0 && (
              <Card title="Shared support devices" subtitle="Jump hosts, servers and traffic generators; never block other testbeds" bodyClassName="">
                <DeviceTable devices={support} all={all} dense />
              </Card>
            )}
          </div>
        )}
        {tab === "schedule" && (
          <Card bodyClassName="p-0">
            <Timeline
              testbeds={[{ id: t.id, name: t.name, status: t.status }, ...t.shares_with.slice(0, 12).map((s) => ({ id: s.id, name: s.name, status: "free" as const }))]}
              onCreate={(tbId, at) => {
                if (tbId === t.id) openBooking(at);
              }}
              highlightId={t.id}
            />
          </Card>
        )}
        {tab === "topology" && (
          <Card bodyClassName="">
            {topoRows.length === 0 ? (
              <EmptyState icon={<FileCode2 className="size-5" />} title="No topology in the testbed file" />
            ) : (
              <table className="w-full text-left text-[12.5px]">
                <thead>
                  <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                    <th className="px-4 py-2 font-semibold">Device</th>
                    <th className="px-2 py-2 font-semibold">Interface</th>
                    <th className="px-2 py-2 font-semibold">Tag</th>
                    <th className="px-2 py-2 font-semibold">Type</th>
                    <th className="px-2 py-2 pr-4 font-semibold">Link</th>
                  </tr>
                </thead>
                <tbody>
                  {topoRows.map((r, i) => (
                    <tr key={i} className="border-b border-line last:border-0">
                      <td className="px-4 py-1.5 font-mono">{r.dev}</td>
                      <td className="px-2 py-1.5 font-mono">{r.iface}</td>
                      <td className="px-2 py-1.5">{r.tag ? <Badge tone="accent" className="font-mono">{r.tag}</Badge> : "—"}</td>
                      <td className="px-2 py-1.5 text-muted">{r.type ?? "—"}</td>
                      <td className="px-2 py-1.5 pr-4 font-mono text-muted">{r.link ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        )}
        {tab === "source" && (
          <Card>
            <SourceView id={t.id} />
          </Card>
        )}
      </div>

      <BookingDialog
        testbed={t}
        open={bookOpen}
        onClose={() => setBookOpen(false)}
        initialStart={bookAt}
        initialDevices={bookDevices}
        initialScope={bookDevices ? "devices" : "testbed"}
      />
      <Modal
        open={maintOpen}
        onClose={() => setMaintOpen(false)}
        title={`Put ${t.name} in maintenance`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setMaintOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={maint.isPending} onClick={() => maint.mutate(true)}>
              Start maintenance
            </Button>
          </>
        }
      >
        <p className="mb-3 text-[13px] text-muted">Nobody can book it until maintenance ends. Existing bookings stay.</p>
        <Input autoFocus placeholder="Why? e.g. replacing PSU on member 2" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
      </Modal>
    </div>
  );
}
