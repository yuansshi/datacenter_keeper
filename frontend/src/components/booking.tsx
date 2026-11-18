import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, CheckCircle2, Clock, Layers, LogOut, Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { duration, timeLeft, toLocalInput, when } from "../lib/format";
import type { Booking, DeviceSummary, TestbedSummary } from "../lib/types";
import { useUser } from "../lib/user";
import { Avatar, Badge, Button, Field, Input, Modal, Segmented, Select, Spinner, StatusDot, TestbedPill, clsx, useToast } from "./ui";

const PRESETS: { label: string; minutes: number | "tomorrow" }[] = [
  { label: "30m", minutes: 30 },
  { label: "1h", minutes: 60 },
  { label: "2h", minutes: 120 },
  { label: "4h", minutes: 240 },
  { label: "8h", minutes: 480 },
  { label: "Until 9 AM tomorrow", minutes: "tomorrow" },
];

function tomorrowNine(from: Date): Date {
  const d = new Date(from);
  d.setDate(d.getDate() + 1);
  d.setHours(9, 0, 0, 0);
  return d;
}

type Pickable = DeviceSummary & { exclusive?: boolean };

/** Devices of a testbed that can be reserved, grouped under their stack. */
function DevicePicker({
  devices,
  stacks,
  selected,
  onChange,
}: {
  devices: Pickable[];
  stacks: DeviceSummary[];
  selected: Set<number>;
  onChange: (next: Set<number>) => void;
}) {
  const user = useUser();
  const exclusive = devices.filter((d) => d.exclusive !== false && !(d.is_virtual && d.member_count > 0));
  const stackById = new Map([...stacks, ...devices.filter((d) => d.is_virtual && d.member_count > 0)].map((s) => [s.id, s]));
  const groups = new Map<number | null, Pickable[]>();
  exclusive.forEach((d) => {
    const key = d.parent_id != null && stackById.has(d.parent_id) ? d.parent_id : null;
    groups.set(key, [...(groups.get(key) ?? []), d]);
  });
  const toggle = (ids: number[], on: boolean) => {
    const next = new Set(selected);
    ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
    onChange(next);
  };
  const row = (d: Pickable, indent = false) => (
    <label key={d.id} className={clsx("flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-panel-3/60", indent && "pl-9")}>
      <input type="checkbox" className="accent-[var(--accent)]" checked={selected.has(d.id)} onChange={(e) => toggle([d.id], e.target.checked)} />
      <StatusDot status={d.status} />
      <span className="font-mono text-[12px]">{d.name}</span>
      {d.stack_role && <span className="text-[11px] text-faint">{d.stack_number != null ? `#${d.stack_number} ` : ""}{d.stack_role}</span>}
      <span className="truncate text-[11.5px] text-faint">{d.model}</span>
      <span className="flex-1" />
      {d.in_use_by && <Badge tone={d.in_use_by === user ? "accent" : "warn"} className="py-0">{d.in_use_by === user ? "yours" : d.in_use_by}</Badge>}
    </label>
  );
  const stacked = [...groups].filter(([k]) => k != null) as [number, Pickable[]][];
  const loose = groups.get(null) ?? [];
  return (
    <div className="max-h-64 overflow-y-auto rounded-lg border border-line bg-panel-2 py-1">
      {stacked
        .sort(([a], [b]) => stackById.get(a)!.name.localeCompare(stackById.get(b)!.name))
        .map(([sid, members]) => {
          const s = stackById.get(sid)!;
          const ids = members.map((m) => m.id);
          const all = ids.every((id) => selected.has(id));
          const some = !all && ids.some((id) => selected.has(id));
          return (
            <div key={sid} className="border-b border-line/60 last:border-0">
              <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-panel-3/60">
                <input
                  type="checkbox"
                  className="accent-[var(--accent)]"
                  checked={all}
                  ref={(el) => {
                    if (el) el.indeterminate = some;
                  }}
                  onChange={(e) => toggle(ids, e.target.checked)}
                />
                <Layers className="size-3.5 text-accent" />
                <span className="font-mono text-[12px] font-semibold">{s.name}</span>
                <span className="text-[11px] text-faint">stack · {members.length}</span>
                <span className="flex-1" />
                {s.in_use_by && <Badge tone="warn" className="py-0">{s.in_use_by}</Badge>}
              </label>
              {members.map((m) => row(m, true))}
            </div>
          );
        })}
      {loose.map((d) => row(d))}
      {exclusive.length === 0 && <p className="px-3 py-2 text-[12px] text-muted">No devices under test in this testbed.</p>}
    </div>
  );
}

export function BookingDialog({
  testbed,
  open,
  onClose,
  initialStart,
  initialDevices,
  initialScope,
  testbeds,
}: {
  testbed: Pick<TestbedSummary, "id" | "name" | "status"> & { shares_with?: { id: number; name: string }[] };
  open: boolean;
  onClose: () => void;
  initialStart?: Date | null;
  /** Preselect devices or stacks (opens in "Choose devices"). */
  initialDevices?: number[] | null;
  initialScope?: "testbed" | "devices";
  /** Let the user pick which of these testbeds the booking is under (device / stack pages). */
  testbeds?: { id: number; name: string }[];
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState<"now" | "later">(initialStart ? "later" : "now");
  const [start, setStart] = useState(() => toLocalInput(initialStart ?? new Date(Date.now() + 60 * 60_000)));
  const [preset, setPreset] = useState<number | "tomorrow" | "custom">(120);
  const [customEnd, setCustomEnd] = useState(() => toLocalInput(new Date(Date.now() + 3 * 60 * 60_000)));
  const [purpose, setPurpose] = useState("");
  const [tbId, setTbId] = useState(testbed.id);
  const [scope, setScope] = useState<"testbed" | "devices">(initialScope ?? (initialDevices?.length ? "devices" : "testbed"));
  const [picked, setPicked] = useState<Set<number>>(() => new Set(initialDevices ?? []));

  useEffect(() => {
    if (open) {
      setMode(initialStart ? "later" : "now");
      if (initialStart) setStart(toLocalInput(initialStart));
      setPurpose("");
      setTbId(testbed.id);
      setScope(initialScope ?? (initialDevices?.length ? "devices" : "testbed"));
      setPicked(new Set(initialDevices ?? []));
    }
  }, [open, initialStart, initialDevices, initialScope, testbed.id]);

  const window_ = useMemo(() => {
    const s = mode === "now" ? new Date() : new Date(start);
    let e: Date;
    if (preset === "custom") e = new Date(customEnd);
    else if (preset === "tomorrow") e = tomorrowNine(s);
    else e = new Date(s.getTime() + preset * 60_000);
    return { s, e };
  }, [mode, start, preset, customEnd]);

  const { data: detail } = useQuery({ queryKey: ["testbed", tbId], queryFn: () => api.testbed(tbId), enabled: open });
  // a stack id in the pick stands for its members in this testbed
  const deviceIds = useMemo(() => {
    if (scope !== "devices") return null;
    const ids = new Set<number>();
    picked.forEach((id) => {
      const members = detail?.devices.filter((d) => d.parent_id === id) ?? [];
      if (members.length && !detail?.devices.some((d) => d.id === id)) members.forEach((m) => ids.add(m.id));
      else ids.add(id);
    });
    return [...ids].sort((a, b) => a - b);
  }, [scope, picked, detail]);
  const valid =
    window_.e > window_.s && !Number.isNaN(window_.s.getTime()) && !Number.isNaN(window_.e.getTime()) && (scope === "testbed" || !!deviceIds?.length);
  const startKey = mode === "now" ? "now" : window_.s.toISOString();
  const { data: avail, isFetching } = useQuery({
    queryKey: ["availability", tbId, startKey, window_.e.toISOString().slice(0, 16), deviceIds?.join(",") ?? "all"],
    queryFn: () => api.availability(tbId, mode === "now" ? null : window_.s.toISOString(), window_.e.toISOString(), deviceIds),
    enabled: open && valid,
  });
  const shares = (tbId === testbed.id ? testbed.shares_with : undefined) ?? detail?.shares_with ?? [];
  const tbName = testbeds?.find((x) => x.id === tbId)?.name ?? detail?.name ?? testbed.name;

  const book = useMutation({
    mutationFn: () =>
      api.book(tbId, {
        starts_at: mode === "now" ? null : window_.s.toISOString(),
        ends_at: window_.e.toISOString(),
        purpose: purpose.trim() || null,
        device_ids: deviceIds,
      }),
    onSuccess: (b) => {
      toast({
        tone: "ok",
        title: b.devices_label ? `Booked ${b.devices_label}` : `Booked ${b.testbed_name}`,
        body: `${b.devices_label ? `${b.testbed_name} · ` : ""}${mode === "now" ? "Now" : when(b.starts_at)} until ${when(b.effective_end)}`,
      });
      qc.invalidateQueries();
      onClose();
    },
    onError: (err) => toast({ tone: "bad", title: "Could not book", body: (err as ApiError).message }),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={
        <span className="flex items-center gap-2">
          Book <span className="font-mono text-accent">{scope === "devices" && avail?.devices_label ? avail.devices_label : tbName}</span>
          {scope === "devices" && avail?.devices_label && <span className="text-[12px] font-normal text-muted">in {tbName}</span>}
        </span>
      }
      width={560}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon={<CalendarClock className="size-4" />}
            disabled={!valid || avail?.available === false}
            loading={book.isPending}
            onClick={() => book.mutate()}
          >
            {mode === "now" ? "Book now" : "Reserve"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {testbeds && testbeds.length > 1 && (
          <Field label="Testbed">
            <Select value={tbId} onChange={(e) => setTbId(Number(e.target.value))} className="w-full">
              {testbeds.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <div>
          <div className="flex items-center justify-between">
            <Segmented
              value={scope}
              onChange={setScope}
              options={[
                { value: "testbed", label: "Whole testbed" },
                { value: "devices", label: "Choose devices" },
              ]}
            />
            {testbed.status !== "free" && tbId === testbed.id && <TestbedPill status={testbed.status} />}
          </div>
          {scope === "devices" && (
            <div className="mt-2">
              {detail ? (
                <DevicePicker devices={detail.devices} stacks={detail.stacks} selected={picked} onChange={setPicked} />
              ) : (
                <p className="flex items-center gap-2 text-[12px] text-muted">
                  <Spinner className="size-3.5" /> Loading devices…
                </p>
              )}
              <p className="mt-1.5 text-[11.5px] text-faint">
                Picking a stack picks all its members. Others can book the rest of {tbName} at the same time.
              </p>
            </div>
          )}
        </div>
        <div className="flex items-center justify-between">
          <Segmented
            value={mode}
            onChange={setMode}
            options={[
              { value: "now", label: "Start now" },
              { value: "later", label: "Schedule" },
            ]}
          />
        </div>
        {mode === "later" && (
          <Field label="Starts">
            <Input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
          </Field>
        )}
        <div>
          <span className="mb-1.5 block text-xs font-medium text-muted">For</span>
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                onClick={() => setPreset(p.minutes)}
                className={clsx(
                  "h-7 rounded-md border px-2.5 text-[12.5px] font-medium transition-colors",
                  preset === p.minutes ? "border-accent bg-accent-soft text-accent" : "border-line bg-panel-2 text-muted hover:text-ink",
                )}
              >
                {p.label}
              </button>
            ))}
            <button
              onClick={() => setPreset("custom")}
              className={clsx(
                "h-7 rounded-md border px-2.5 text-[12.5px] font-medium",
                preset === "custom" ? "border-accent bg-accent-soft text-accent" : "border-line bg-panel-2 text-muted hover:text-ink",
              )}
            >
              Custom…
            </button>
          </div>
          {preset === "custom" && (
            <Input className="mt-2" type="datetime-local" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} />
          )}
          {window_.e > window_.s && (
            <p className="mt-2 flex items-center gap-1.5 text-[12px] text-muted">
              <Clock className="size-3.5" />
              {mode === "now" ? "Now" : when(window_.s.toISOString())} → {when(window_.e.toISOString())} · {duration(window_.e.getTime() - window_.s.getTime())}
            </p>
          )}
        </div>
        <Field label="Purpose (optional)">
          <Input value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="e.g. MS 17.2 smoke run" maxLength={300} />
        </Field>

        <div className="rounded-lg border border-line bg-panel-2 p-3 text-[12.5px]">
          {scope === "devices" && !deviceIds?.length ? (
            <p className="text-muted">Pick at least one stack or device.</p>
          ) : !valid ? (
            <p className="text-bad">The end must be after the start.</p>
          ) : isFetching && !avail ? (
            <p className="flex items-center gap-2 text-muted">
              <Spinner className="size-3.5" /> Checking availability…
            </p>
          ) : avail?.error ? (
            <p className="flex items-center gap-2 text-bad">
              <AlertTriangle className="size-4" /> {avail.error}
            </p>
          ) : avail?.available ? (
            <p className="flex items-center gap-2 text-ok">
              <CheckCircle2 className="size-4" /> Available for this window
              {scope === "devices" && avail.device_ids?.length === 0 && <span className="text-muted"> (that is every device: books the whole testbed)</span>}
            </p>
          ) : avail ? (
            <div>
              <p className="flex items-center gap-2 font-medium text-bad">
                <AlertTriangle className="size-4" /> Conflicts with
              </p>
              <ul className="mt-1.5 space-y-1">
                {avail.conflicts.map((c) => (
                  <li key={c.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-muted">
                    <Avatar name={c.owner} size={18} />
                    <span className="text-ink">{c.owner}</span> has
                    {c.devices_label && <span className="font-mono text-ink">{c.devices_label} in</span>}
                    <Link to={`/testbeds/${c.testbed_id}`} className="font-mono text-accent hover:underline" onClick={onClose}>
                      {c.testbed_name}
                    </Link>
                    until {when(c.effective_end)}
                  </li>
                ))}
                {avail.conflicts.length === 0 && <li className="text-muted">The testbed is in maintenance.</li>}
              </ul>
            </div>
          ) : null}
          {shares.length > 0 && scope === "testbed" && (
            <p className="mt-2 border-t border-line pt-2 text-faint">
              Shares hardware with {shares.length} other testbed{shares.length === 1 ? "" : "s"}
              {": "}
              <span className="font-mono text-muted">{shares.slice(0, 6).map((s) => s.name).join(", ")}</span>
              {shares.length > 6 && ` +${shares.length - 6}`}. They show as blocked while you hold this one.
            </p>
          )}
        </div>
      </div>
    </Modal>
  );
}

/** Release / extend buttons for a booking, respecting ownership. */
export function BookingActions({ booking, size = "xs", compact }: { booking: Booking; size?: "xs" | "sm"; compact?: boolean }) {
  const user = useUser();
  const qc = useQueryClient();
  const toast = useToast();
  const mine = booking.owner === user;
  const [confirmForce, setConfirmForce] = useState(false);
  const release = useMutation({
    mutationFn: (force: boolean) => api.release(booking.id, force),
    onSuccess: (b) => {
      const what = b.devices_label ? `${b.devices_label} in ${b.testbed_name}` : b.testbed_name;
      toast({
        tone: "ok",
        title: b.state === "cancelled" ? `Cancelled ${what}` : `Released ${what}`,
        body: b.owner !== user ? `${b.owner}'s booking · logged under your name` : undefined,
      });
      qc.invalidateQueries();
      setConfirmForce(false);
    },
    onError: (e) => toast({ tone: "bad", title: "Could not release", body: (e as Error).message }),
  });
  const extend = useMutation({
    mutationFn: (minutes: number) => api.extend(booking.id, minutes),
    onSuccess: (b) => {
      toast({ tone: "ok", title: `Extended ${b.testbed_name}`, body: `Now until ${when(b.effective_end)}` });
      qc.invalidateQueries();
    },
    onError: (e) => toast({ tone: "bad", title: "Could not extend", body: (e as Error).message }),
  });
  if (!["active", "upcoming"].includes(booking.state)) return null;
  return (
    <div className="flex items-center gap-1.5">
      {mine ? (
        <>
          <Button size={size} icon={<Plus className="size-3.5" />} loading={extend.isPending} onClick={() => extend.mutate(60)} title="Extend by 1 hour">
            {compact ? "1h" : "Extend 1h"}
          </Button>
          <Button size={size} variant="danger" icon={<LogOut className="size-3.5" />} loading={release.isPending} onClick={() => release.mutate(false)}>
            {booking.state === "upcoming" ? "Cancel" : "Release"}
          </Button>
        </>
      ) : (
        <>
          <Button size={size} variant="ghost" icon={<LogOut className="size-3.5" />} onClick={() => setConfirmForce(true)} title={`Courtesy release of ${booking.owner}'s booking`}>
            {booking.state === "upcoming" ? "Cancel" : "Release"}
          </Button>
          <Modal
            open={confirmForce}
            onClose={() => setConfirmForce(false)}
            title={booking.state === "upcoming" ? `Cancel ${booking.owner}'s booking?` : `Release ${booking.owner}'s booking?`}
            footer={
              <>
                <Button variant="ghost" onClick={() => setConfirmForce(false)}>
                  Keep it
                </Button>
                <Button variant="danger" loading={release.isPending} onClick={() => release.mutate(true)}>
                  {booking.state === "upcoming" ? "Cancel it" : "Release it"}
                </Button>
              </>
            }
          >
            <p className="text-[13px] text-muted">
              Courtesy release: anyone can free hardware that is booked but not in use.{" "}
              <span className="text-ink">{booking.owner}</span> holds{" "}
              <span className="font-mono text-ink">{booking.devices_label ? `${booking.devices_label} in ${booking.testbed_name}` : booking.testbed_name}</span>
              {booking.state === "upcoming" ? ` from ${when(booking.starts_at)}` : ` for another ${timeLeft(booking.effective_end)}`}. It is logged under your
              name, so let them know.
            </p>
          </Modal>
        </>
      )}
    </div>
  );
}

export function BookingLine({ booking, showTestbed = true }: { booking: Booking; showTestbed?: boolean }) {
  const active = booking.state === "active";
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <Avatar name={booking.owner} size={24} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px]">
          {showTestbed && (
            <Link to={`/testbeds/${booking.testbed_id}`} className="font-mono font-medium text-ink hover:text-accent">
              {booking.testbed_name}
            </Link>
          )}
          {showTestbed && <span className="text-faint"> · </span>}
          <span className="text-muted">{booking.owner}</span>
          {booking.devices_label && <span className="ml-1.5 rounded bg-info/12 px-1.5 py-0.5 font-mono text-[11px] text-info">{booking.devices_label}</span>}
        </p>
        <p className="truncate text-[11.5px] text-faint">
          {active ? `${timeLeft(booking.effective_end)} left · until ${when(booking.effective_end)}` : `${when(booking.starts_at)} → ${when(booking.effective_end)}`}
          {booking.purpose && ` · ${booking.purpose}`}
        </p>
      </div>
    </div>
  );
}
