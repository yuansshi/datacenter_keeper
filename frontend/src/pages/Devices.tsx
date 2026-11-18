import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Layers, Search, Server, Terminal } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Badge, Card, EmptyState, HealthBar, Input, PageHeader, Segmented, Select, Skeleton, StatusDot, clsx } from "../components/ui";
import { api } from "../lib/api";
import { downCount, relative, roleLabel, STATUS_LABEL, upCount } from "../lib/format";
import type { DeviceStatus, DeviceSummary, StatusCounts } from "../lib/types";
import { readPref, writePref } from "../lib/user";

type GroupBy = "none" | "network" | "testbed" | "rack" | "role";
const STATUSES: DeviceStatus[] = ["online", "alerting", "offline", "dormant", "unknown"];

function counts(devs: DeviceSummary[]): StatusCounts {
  const c: StatusCounts = { online: 0, alerting: 0, offline: 0, dormant: 0, unknown: 0 };
  devs.forEach((d) => (c[d.status] = (c[d.status] ?? 0) + 1));
  return c;
}

type StackRow = { device: DeviceSummary; members: DeviceSummary[] };

const byNumber = (a: DeviceSummary, b: DeviceSummary) =>
  (a.stack_number ?? 99) - (b.stack_number ?? 99) || a.name.localeCompare(b.name, undefined, { numeric: true });

/** One row per stack (its members under it) and one per stand-alone device. `all` resolves each member's stack. */
export function stackRows(devices: DeviceSummary[], all: DeviceSummary[] = devices): StackRow[] {
  const byId = new Map(all.map((d) => [d.id, d]));
  const grouped = new Map<number, DeviceSummary[]>();
  const loose: DeviceSummary[] = [];
  for (const d of devices) {
    const parent = d.parent_id != null ? byId.get(d.parent_id) : undefined;
    if (parent?.is_virtual) grouped.set(parent.id, [...(grouped.get(parent.id) ?? []), d]);
    else loose.push(d);
  }
  const rows: StackRow[] = [...grouped].map(([id, members]) => ({ device: byId.get(id)!, members: members.sort(byNumber) }));
  for (const d of loose) {
    if (!d.is_virtual || d.member_count === 0) rows.push({ device: d, members: [] });
    else if (!grouped.has(d.id)) rows.push({ device: d, members: all.filter((m) => m.parent_id === d.id).sort(byNumber) });
  }
  return rows.sort((a, b) => a.device.name.localeCompare(b.device.name, undefined, { numeric: true }));
}

/** A stack listed with its members counts once, as its members. */
export const isStackWithMembers = (d: DeviceSummary) => d.is_virtual && d.member_count > 0;

function modelSummary(members: DeviceSummary[]): { text: string; title?: string } {
  const counts = new Map<string, number>();
  members.forEach((m) => m.model && counts.set(m.model, (counts.get(m.model) ?? 0) + 1));
  const list = [...counts].sort((a, b) => b[1] - a[1]);
  if (list.length === 0) return { text: "—" };
  const title = list.map(([m, n]) => (n > 1 ? `${n} × ${m}` : m)).join(", ");
  if (list.length === 1) return { text: list[0][1] > 1 ? `${list[0][1]} × ${list[0][0]}` : list[0][0], title };
  return { text: `${list[0][0]} +${list.length - 1}`, title };
}

function rackSummary(members: DeviceSummary[]): string {
  const racked = members.filter((m) => m.rack);
  if (racked.length === 0) return "—";
  const racks = new Set(racked.map((m) => m.rack!.name));
  if (racks.size > 1) return `${racks.size} racks`;
  const us = racked.map((m) => m.rack_position).filter((u): u is number => u != null);
  const span = us.length ? (Math.min(...us) === Math.max(...us) ? ` · U${us[0]}` : ` · U${Math.min(...us)}–${Math.max(...us)}`) : "";
  return `${racked[0].rack!.name}${span}`;
}

const ROLE_TONE: Record<string, "accent" | "info" | "neutral"> = { active: "accent", standby: "info", member: "neutral" };

export function StackRole({ d }: { d: Pick<DeviceSummary, "stack_role" | "stack_number"> }) {
  if (!d.stack_role && d.stack_number == null) return null;
  return (
    <Badge tone={ROLE_TONE[d.stack_role ?? ""] ?? "neutral"} className="py-0">
      {d.stack_number != null && `#${d.stack_number} `}
      {d.stack_role ?? "member"}
    </Badge>
  );
}

export function DeviceTable({
  devices,
  all,
  showTestbeds = true,
  dense,
  expandAll,
}: {
  devices: DeviceSummary[];
  all?: DeviceSummary[];
  showTestbeds?: boolean;
  dense?: boolean;
  expandAll?: boolean;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const rows = useMemo(() => stackRows(devices, all ?? devices), [devices, all]);
  const pad = dense ? "py-1.5" : "py-2.5";

  const deviceRow = (d: DeviceSummary, member = false) => (
    <tr
      key={d.id}
      onClick={() => navigate(`/devices/${d.id}`)}
      className={clsx(
        "cursor-pointer border-b border-line hover:bg-panel-2/70",
        member && "bg-panel-2/30",
        d.removed && "opacity-50",
      )}
    >
      <td className={clsx("pl-4 pr-2", pad)}>
        <div className={clsx("flex items-center gap-2.5", member && "pl-6")}>
          {member && <span aria-hidden className="-ml-3 h-3 w-2.5 shrink-0 -translate-y-1 rounded-bl border-b border-l border-line-strong/70" />}
          <StatusDot status={d.status} pulse />
          <div className="min-w-0">
            <Link to={`/devices/${d.id}`} className="font-mono font-medium text-ink hover:text-accent" onClick={(e) => e.stopPropagation()}>
              {d.name}
            </Link>
            <p className="flex items-center gap-1.5 text-[11px] text-faint">
              {roleLabel(d.role)}
              {member && <StackRole d={d} />}
              {d.is_virtual && !member && <Badge className="py-0">stack</Badge>}
              {d.is_shared && <Badge className="py-0">shared</Badge>}
              {d.has_console && <Terminal className="size-3" aria-label="has console" />}
            </p>
          </div>
        </div>
      </td>
      <td className="px-2" title={d.status_detail ?? undefined}>
        <span className="text-muted">{STATUS_LABEL[d.status]}</span>
        {d.status !== "online" && d.status_changed_at && <span className="block text-[11px] text-faint" title="When Labkeeper first recorded this status">Recorded {relative(d.status_changed_at)}</span>}
      </td>
      <td className="px-2 text-muted">{d.model ?? "—"}</td>
      <td className="px-2 font-mono text-[11.5px] text-muted">{d.serial ?? "—"}</td>
      <td className="hidden max-w-[180px] truncate px-2 text-muted lg:table-cell">{d.network?.name ?? "—"}</td>
      <td className="hidden whitespace-nowrap px-2 font-mono text-[11.5px] text-muted md:table-cell">
        {d.rack ? `${d.rack.name}${d.rack_position ? ` · U${d.rack_position}` : ""}` : "—"}
      </td>
      {showTestbeds && <td className="num hidden px-2 text-muted xl:table-cell">{d.testbed_count || "—"}</td>}
      <td className="px-2 pr-4">{d.in_use_by ? <Badge tone="warn">{d.in_use_by}</Badge> : <span className="text-faint">—</span>}</td>
    </tr>
  );

  const stackRow = ({ device: s, members }: StackRow) => {
    const expanded = open[s.id] ?? !!expandAll;
    const c = counts(members);
    const model = modelSummary(members);
    const toggle = (e: { stopPropagation: () => void }) => {
      e.stopPropagation();
      setOpen((cur) => ({ ...cur, [s.id]: !expanded }));
    };
    return [
      <tr
        key={`s${s.id}`}
        onClick={() => navigate(`/devices/${s.id}`)}
        className={clsx("cursor-pointer border-b border-line hover:bg-panel-2/70", expanded && "bg-panel-2/40")}
      >
        <td className={clsx("pl-1.5 pr-2", pad)}>
          <div className="flex items-center gap-1.5">
            <button
              onClick={toggle}
              className="flex size-6 shrink-0 items-center justify-center rounded-md text-faint hover:bg-panel-3 hover:text-ink"
              aria-label={expanded ? `Hide ${s.name} members` : `Show ${s.name} members`}
              aria-expanded={expanded}
            >
              {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
            </button>
            <span className="flex size-6 shrink-0 items-center justify-center rounded-md border border-accent/30 bg-accent-soft text-accent">
              <Layers className="size-3.5" />
            </span>
            <div className="min-w-0">
              <Link to={`/devices/${s.id}`} className="font-mono font-medium text-ink hover:text-accent" onClick={(e) => e.stopPropagation()}>
                {s.name}
              </Link>
              <p className="flex items-center gap-1.5 text-[11px] text-faint">
                Switch stack · {members.length} {members.length === 1 ? "member" : "members"}
                {members.some((m) => m.has_console) && <Terminal className="size-3" aria-label="has console" />}
              </p>
            </div>
          </div>
        </td>
        <td className="px-2" title={s.status_detail ?? undefined}>
          <span className="flex items-center gap-1.5 text-muted">
            <StatusDot status={s.status} pulse /> {STATUS_LABEL[s.status]}
          </span>
          <span className="mt-1 flex items-center gap-1.5">
            <HealthBar health={c} className="w-14" />
            <span className="num text-[11px] text-faint">
              {upCount(c)}/{members.length} up
            </span>
          </span>
        </td>
        <td className="px-2 text-muted" title={model.title}>
          {model.text}
        </td>
        <td className="px-2 text-[11.5px] text-faint">{members.length} serials</td>
        <td className="hidden max-w-[180px] truncate px-2 text-muted lg:table-cell">{s.network?.name ?? members[0]?.network?.name ?? "—"}</td>
        <td className="hidden whitespace-nowrap px-2 font-mono text-[11.5px] text-muted md:table-cell">{rackSummary(members)}</td>
        {showTestbeds && <td className="num hidden px-2 text-muted xl:table-cell">{s.testbed_count || "—"}</td>}
        <td className="px-2 pr-4">{s.in_use_by ? <Badge tone="warn">{s.in_use_by}</Badge> : <span className="text-faint">—</span>}</td>
      </tr>,
      ...(expanded ? members.map((m) => deviceRow(m, true)) : []),
    ];
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-[12.5px]">
        <thead>
          <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
            <th className="py-2 pl-4 pr-2 font-semibold">Device</th>
            <th className="px-2 py-2 font-semibold">Status</th>
            <th className="px-2 py-2 font-semibold">Model</th>
            <th className="px-2 py-2 font-semibold">Serial</th>
            <th className="hidden px-2 py-2 font-semibold lg:table-cell">Network</th>
            <th className="hidden px-2 py-2 font-semibold md:table-cell">Rack</th>
            {showTestbeds && <th className="hidden px-2 py-2 font-semibold xl:table-cell">Testbeds</th>}
            <th className="px-2 py-2 pr-4 font-semibold">In use by</th>
          </tr>
        </thead>
        <tbody>{rows.flatMap((r) => (r.device.is_virtual && r.members.length ? stackRow(r) : [deviceRow(r.device)]))}</tbody>
      </table>
    </div>
  );
}

export default function Devices() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const status = params.get("status") ?? "";
  const role = params.get("role") ?? "";
  const network = params.get("network") ?? "";
  const testbed = params.get("testbed") ?? "";
  const [groupBy, setGroupBy] = useState<GroupBy>(() => (params.get("group") as GroupBy) || readPref("devices.group", "none"));
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const { data: devices, isLoading } = useQuery({
    queryKey: ["devices", { role, network, testbed }],
    queryFn: () =>
      api.devices({
        role: role || undefined,
        network_id: network ? Number(network) : undefined,
        testbed_id: testbed ? Number(testbed) : undefined,
      }),
    refetchInterval: 20_000,
  });
  const { data: networks } = useQuery({ queryKey: ["networks"], queryFn: api.networks });
  const { data: memberships } = useQuery({ queryKey: ["memberships"], queryFn: api.memberships, enabled: groupBy === "testbed" || !!testbed });

  const all = useMemo(() => devices ?? [], [devices]);
  const physical = useMemo(() => all.filter((d) => !isStackWithMembers(d)), [all]);
  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    const wanted = status ? new Set(status.split(",")) : null;
    const stackName = new Map(all.filter(isStackWithMembers).map((s) => [s.id, s.name.toLowerCase()]));
    return physical.filter(
      (d) =>
        (!wanted || wanted.has(d.status)) &&
        (!term ||
          [d.name, d.serial, d.model, d.mgmt_ip, d.alias, d.network?.name, d.rack?.name].some((v) => v?.toLowerCase().includes(term)) ||
          (d.parent_id != null && stackName.get(d.parent_id)?.includes(term))),
    );
  }, [all, physical, q, status]);
  const expandAll = !!q.trim() || !!status;

  const statusCounts = useMemo(() => counts(physical), [physical]);
  const stackCount = useMemo(() => all.filter(isStackWithMembers).length, [all]);
  const roles = useMemo(() => Array.from(new Set((devices ?? []).map((d) => d.role))).sort(), [devices]);

  const groups = useMemo(() => {
    if (groupBy === "none") return [{ key: "all", label: "", devices: filtered }];
    const map = new Map<string, { key: string; label: string; to?: string; devices: DeviceSummary[] }>();
    const add = (key: string, label: string, d: DeviceSummary, to?: string) => {
      if (!map.has(key)) map.set(key, { key, label, to, devices: [] });
      map.get(key)!.devices.push(d);
    };
    if (groupBy === "testbed") {
      const byId = new Map(filtered.map((d) => [d.id, d]));
      const seen = new Set<number>();
      memberships?.forEach((m) => {
        m.device_ids.forEach((id) => {
          const d = byId.get(id);
          if (d) {
            seen.add(id);
            add(`t${m.testbed_id}`, m.name, d, `/testbeds/${m.testbed_id}`);
          }
        });
      });
      filtered.filter((d) => !seen.has(d.id)).forEach((d) => add("none", "Not in any testbed", d));
    } else {
      filtered.forEach((d) => {
        if (groupBy === "network") add(d.network ? `n${d.network.id}` : "none", d.network?.name ?? "No network", d);
        else if (groupBy === "rack") add(d.rack ? `r${d.rack.id}` : "none", d.rack?.name ?? "Not racked", d, d.rack ? `/racks/${d.rack.id}` : undefined);
        else add(d.role, roleLabel(d.role), d);
      });
    }
    return Array.from(map.values()).sort((a, b) => (a.key === "none" ? 1 : b.key === "none" ? -1 : a.label.localeCompare(b.label)));
  }, [filtered, groupBy, memberships]);

  const toggleStatus = (s: DeviceStatus) => {
    const cur = new Set(status ? status.split(",") : []);
    if (cur.has(s)) cur.delete(s);
    else cur.add(s);
    set("status", Array.from(cur).join(","));
  };

  return (
    <div>
      <PageHeader
        title="Devices"
        subtitle={
          devices
            ? `${physical.length} devices${stackCount ? ` · ${stackCount} stacks` : ""} from your testbed files · live status every minute`
            : "Loading…"
        }
        icon={<Server className="size-5" />}
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          icon={<Search className="size-4" />}
          placeholder="Filter by name, serial, model, IP, rack"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            set("q", e.target.value);
          }}
          className="w-full sm:w-72"
        />
        <div className="flex flex-wrap gap-1">
          {STATUSES.map((s) => {
            const on = status.split(",").includes(s);
            return (
              <button
                key={s}
                onClick={() => toggleStatus(s)}
                className={clsx(
                  "flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[12.5px] font-medium transition-colors",
                  on ? "border-accent/60 bg-accent-soft text-ink" : "border-line bg-panel-2 text-muted hover:text-ink",
                )}
              >
                <StatusDot status={s} />
                {STATUS_LABEL[s]}
                <span className="num text-[11px] text-faint">{statusCounts[s]}</span>
              </button>
            );
          })}
        </div>
        <Select value={role} onChange={(e) => set("role", e.target.value)}>
          <option value="">All roles</option>
          {roles.map((r) => (
            <option key={r} value={r}>
              {roleLabel(r)}
            </option>
          ))}
        </Select>
        <Select value={network} onChange={(e) => set("network", e.target.value)} className="max-w-48">
          <option value="">All networks</option>
          {networks?.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name}
            </option>
          ))}
        </Select>
        <div className="flex-1" />
        <div className="flex items-center gap-2">
          <Layers className="size-4 text-faint" />
          <Segmented
            size="xs"
            value={groupBy}
            onChange={(g) => {
              setGroupBy(g);
              writePref("devices.group", g);
            }}
            options={[
              { value: "none", label: "No grouping" },
              { value: "network", label: "Network" },
              { value: "testbed", label: "Testbed" },
              { value: "rack", label: "Rack" },
              { value: "role", label: "Role" },
            ]}
          />
        </div>
      </div>

      {isLoading ? (
        <Skeleton className="h-96" />
      ) : filtered.length === 0 ? (
        <Card>
          <EmptyState icon={<Server className="size-5" />} title="No devices match">
            Clear a filter, or add testbed folders on the Testbed files page.
          </EmptyState>
        </Card>
      ) : groupBy === "none" ? (
        <Card bodyClassName="">
          <DeviceTable devices={filtered} all={all} expandAll={expandAll} />
        </Card>
      ) : (
        <div className="space-y-3">
          {groups.map((g) => {
            const open = !collapsed[g.key];
            const c = counts(g.devices);
            return (
              <Card key={g.key} bodyClassName="">
                <button
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left"
                  onClick={() => setCollapsed((s) => ({ ...s, [g.key]: open }))}
                >
                  {open ? <ChevronDown className="size-4 text-faint" /> : <ChevronRight className="size-4 text-faint" />}
                  <span className="font-mono text-[13px] font-semibold">{g.label}</span>
                  <span className="num text-[12px] text-faint">{g.devices.length}</span>
                  <HealthBar health={c} className="w-28" />
                  {downCount(c) > 0 && <Badge tone="bad">{downCount(c)} down</Badge>}
                  <span className="flex-1" />
                  {g.to && (
                    <Link to={g.to} onClick={(e) => e.stopPropagation()} className="text-[12px] text-accent hover:underline">
                      Open
                    </Link>
                  )}
                </button>
                {open && (
                  <div className="border-t border-line">
                    <DeviceTable devices={g.devices} all={all} showTestbeds={groupBy !== "testbed"} dense expandAll={expandAll} />
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
