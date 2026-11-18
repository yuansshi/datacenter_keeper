import { useQuery } from "@tanstack/react-query";
import { CalendarPlus, FlaskConical, Lock, Search, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { BookingActions, BookingDialog } from "../components/booking";
import { CloudAvailability } from "../components/CloudAvailability";
import { Avatar, Badge, Button, Card, EmptyState, HealthBar, Input, PageHeader, Segmented, Skeleton, StatusDot, TestbedPill, clsx } from "../components/ui";
import { api } from "../lib/api";
import { TESTBED_COLOR, timeLeft, when } from "../lib/format";
import type { TestbedStatus, TestbedSummary } from "../lib/types";
import { readPref, useUser, writePref } from "../lib/user";

type Filter = "all" | TestbedStatus | "mine";

export function TestbedCard({ t, onBook }: { t: TestbedSummary; onBook: (t: TestbedSummary) => void }) {
  const user = useUser();
  const mine = t.current?.owner === user;
  return (
    <div
      className={clsx(
        "group relative flex flex-col rounded-xl border bg-panel p-4 shadow-panel transition-colors",
        mine ? "border-accent/50" : "border-line hover:border-line-strong",
      )}
    >
      <span className="absolute inset-y-3 left-0 w-[3px] rounded-r-full" style={{ background: TESTBED_COLOR[t.status] }} />
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link to={`/testbeds/${t.id}`} className="block truncate font-mono text-[14px] font-semibold hover:text-accent">
            {t.name}
          </Link>
          <p className="truncate text-[11.5px] text-faint">
            {[t.group, t.kind].filter(Boolean).join(" · ") || t.key}
          </p>
        </div>
        <TestbedPill status={t.status} />
      </div>
      <div className="mt-3 flex flex-wrap gap-1">
        {t.models.slice(0, 4).map((m) => (
          <Badge key={m} className="font-mono">
            {m}
          </Badge>
        ))}
        {t.models.length > 4 && <Badge>+{t.models.length - 4}</Badge>}
      </div>
      <div className="mt-3 flex items-center gap-2 text-[11.5px] text-muted">
        <HealthBar health={t.health} className="flex-1" />
        <span className="num">{t.exclusive_count} devices</span>
      </div>
      <p className="mt-1.5 flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true"><StatusDot status="online" /></span>{t.health.online} online</span>
        <span className={clsx("inline-flex items-center gap-1.5", t.health.alerting && "text-warn")}><span aria-hidden="true"><StatusDot status="alerting" /></span>{t.health.alerting} alerting</span>
        <span className={clsx("inline-flex items-center gap-1.5", t.health.offline && "text-bad")}><span aria-hidden="true"><StatusDot status="offline" /></span>{t.health.offline} offline</span>
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true"><StatusDot status="dormant" /></span>{t.health.dormant} dormant</span>
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true"><StatusDot status="unknown" /></span>{t.health.unknown} unknown</span>
      </p>
      <div className="mt-3 min-h-10 flex-1 text-[12px]">
        {t.status === "in_use" && t.current && (
          <div className="flex items-center gap-2">
            <Avatar name={t.current.owner} size={22} />
            <div className="min-w-0">
              <p className="truncate">
                <span className="font-medium">{mine ? "You" : t.current.owner}</span>
                <span className="text-muted"> · {timeLeft(t.current.effective_end)} left</span>
              </p>
              <p className="truncate text-faint">{t.current.purpose ?? `until ${when(t.current.effective_end)}`}</p>
            </div>
          </div>
        )}
        {t.status === "partial" && (
          <div className="space-y-1">
            <p className="text-muted">
              <span className="num text-ink">{t.busy_count}</span> of {t.exclusive_count} devices in use · the rest can be booked
            </p>
            {t.partial.slice(0, 2).map((b) => (
              <p key={b.id} className="flex items-center gap-1.5 truncate">
                <Avatar name={b.owner} size={16} />
                <span className="font-medium">{b.owner === user ? "You" : b.owner}</span>
                <span className="truncate font-mono text-[11px] text-muted">{b.devices_label}</span>
                <span className="text-faint">· {timeLeft(b.effective_end)}</span>
              </p>
            ))}
          </div>
        )}
        {t.status === "blocked" && t.blocked_by[0] && (
          <p className="flex items-start gap-1.5 text-muted">
            <Lock className="mt-0.5 size-3.5 shrink-0 text-violet" />
            <span>
              Shares hardware with <span className="font-mono text-ink">{t.blocked_by[0].testbed_name}</span> ({t.blocked_by[0].owner}, free{" "}
              {when(t.blocked_by[0].effective_end)})
            </span>
          </p>
        )}
        {t.status === "maintenance" && (
          <p className="flex items-start gap-1.5 text-muted">
            <Wrench className="mt-0.5 size-3.5 shrink-0" />
            {t.maintenance_note ?? "In maintenance"}
          </p>
        )}
        {t.status === "free" && (
          <p className="text-muted">{t.next ? `Free until ${when(t.next.starts_at)} (${t.next.owner})` : "Free, nothing scheduled"}</p>
        )}
      </div>
      <div className="mt-3 flex items-center justify-end gap-1.5 border-t border-line pt-3">
        {t.current && (t.current.owner === user || t.status === "in_use") ? (
          <BookingActions booking={t.current} compact />
        ) : null}
        {!t.current && t.partial.length === 1 && <BookingActions booking={t.partial[0]} compact />}
        {(t.status === "free" || t.status === "blocked" || t.status === "partial") && (
          <Button size="xs" variant={t.status === "free" ? "primary" : "secondary"} icon={<CalendarPlus className="size-3.5" />} onClick={() => onBook(t)}>
            {t.status === "free" ? "Book" : t.status === "partial" ? "Book devices" : "Schedule"}
          </Button>
        )}
      </div>
    </div>
  );
}

export default function Testbeds() {
  const [params, setParams] = useSearchParams();
  const filter = (params.get("status") as Filter) || "all";
  const [q, setQ] = useState("");
  const [booking, setBooking] = useState<TestbedSummary | null>(null);
  const [grouped, setGrouped] = useState<boolean | null>(() => readPref<boolean | null>("testbeds.grouped", null));
  const user = useUser();
  const { data, isLoading } = useQuery({ queryKey: ["testbeds"], queryFn: () => api.testbeds(), refetchInterval: 15_000 });
  const system = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 15_000 });
  const [cloudClock, setCloudClock] = useState({ updatedAt: 0, elapsedSeconds: 0 });
  useEffect(() => {
    const updatedAt = system.dataUpdatedAt;
    const initialAge = updatedAt ? Math.max(0, (Date.now() - updatedAt) / 1_000) : 0;
    const started = performance.now();
    const tick = () => setCloudClock({ updatedAt, elapsedSeconds: initialAge + (performance.now() - started) / 1_000 });
    tick();
    const timer = window.setInterval(tick, 1_000);
    return () => window.clearInterval(timer);
  }, [system.dataUpdatedAt]);
  const cloudElapsedSeconds = cloudClock.updatedAt === system.dataUpdatedAt
    ? cloudClock.elapsedSeconds
    : system.dataUpdatedAt ? Math.max(0, (Date.now() - system.dataUpdatedAt) / 1_000) : 0;

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: data?.length ?? 0, free: 0, in_use: 0, partial: 0, blocked: 0, maintenance: 0, mine: 0 };
    data?.forEach((t) => {
      c[t.status]++;
      if (t.current?.owner === user || t.partial.some((b) => b.owner === user)) c.mine++;
    });
    return c;
  }, [data, user]);

  const groups = useMemo(() => {
    const term = q.trim().toLowerCase();
    const list = (data ?? []).filter(
      (t) =>
        (filter === "all" ||
          (filter === "mine" ? t.current?.owner === user || t.partial.some((b) => b.owner === user) : t.status === filter)) &&
        (!term || t.name.toLowerCase().includes(term) || t.models.some((m) => m.toLowerCase().includes(term)) || (t.group ?? "").toLowerCase().includes(term)),
    );
    const byFolder = grouped ?? (data?.length ?? 0) > 16;
    if (!byFolder) return list.length ? ([["", list]] as [string, TestbedSummary[]][]) : [];
    const map = new Map<string, TestbedSummary[]>();
    list.forEach((t) => {
      const g = t.group ?? "Other";
      map.set(g, [...(map.get(g) ?? []), t]);
    });
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [data, filter, q, user, grouped]);

  return (
    <div>
      <PageHeader
        title="Testbeds"
        subtitle="One testbed per pyATS file. Book all of it, or just a stack or a switch; bookings block every testbed that shares those switches."
        icon={<FlaskConical className="size-5" />}
      />
      <CloudAvailability system={system.data} failed={system.isError} paused={system.fetchStatus === "paused"} elapsedSeconds={cloudElapsedSeconds} />
      <p className="mb-3 text-xs leading-relaxed text-muted">
        Testbed cards show the latest recorded physical-device health alongside booking availability.
        Shared testbed definitions can repeat the same devices and their health.
      </p>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Segmented
          value={filter}
          onChange={(v) => {
            const next = new URLSearchParams(params);
            if (v === "all") next.delete("status");
            else next.set("status", v);
            setParams(next, { replace: true });
          }}
          options={[
            { value: "all", label: "All", count: counts.all },
            { value: "free", label: "Free", count: counts.free },
            { value: "in_use", label: "In use", count: counts.in_use },
            { value: "partial", label: "Partly in use", count: counts.partial },
            { value: "blocked", label: "Blocked", count: counts.blocked },
            { value: "maintenance", label: "Maintenance", count: counts.maintenance },
            { value: "mine", label: "Mine", count: counts.mine },
          ]}
        />
        <Input icon={<Search className="size-4" />} placeholder="Name, folder or device model" value={q} onChange={(e) => setQ(e.target.value)} className="w-full sm:w-72" />
        <label className="ml-auto flex cursor-pointer items-center gap-2 text-[12.5px] text-muted">
          <input
            type="checkbox"
            className="accent-[var(--accent)]"
            checked={grouped ?? (data?.length ?? 0) > 16}
            onChange={(e) => {
              setGrouped(e.target.checked);
              writePref("testbeds.grouped", e.target.checked);
            }}
          />
          Group by folder
        </label>
      </div>
      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-52" />
          ))}
        </div>
      ) : groups.length === 0 ? (
        <Card>
          <EmptyState icon={<FlaskConical className="size-5" />} title="No testbeds here">
            {data?.length ? "Try another filter." : "Point Labkeeper at your pyATS testbed folder on the Testbed files page."}
          </EmptyState>
        </Card>
      ) : (
        <div className="space-y-6">
          {groups.map(([group, items]) => (
            <section key={group}>
              {group && (
                <h2 className="mb-2 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wider text-faint">
                  {group} <span className="num font-normal">{items.length}</span>
                </h2>
              )}
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {items.map((t) => (
                  <TestbedCard key={t.id} t={t} onBook={setBooking} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
      {booking && <BookingDialog testbed={booking} open onClose={() => setBooking(null)} initialStart={booking.status === "blocked" && booking.blocked_by[0] ? new Date(booking.blocked_by[0].effective_end) : null} />}
    </div>
  );
}
