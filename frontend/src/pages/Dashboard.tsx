import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, CircleAlert, FlaskConical, Map as MapIcon, Rows3, Server, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { BookingActions, BookingLine } from "../components/booking";
import { ActivityTable } from "./Activity";
import { Badge, Card, EmptyState, HealthBar, Skeleton, StatusDot, clsx } from "../components/ui";
import { api } from "../lib/api";
import { relative, roleLabel } from "../lib/format";
import { useUser } from "../lib/user";

function Stat({ label, value, sub, to, icon, accent, children }: { label: string; value: ReactNode; sub?: ReactNode; to: string; icon: ReactNode; accent?: string; children?: ReactNode }) {
  return (
    <Link
      to={to}
      className="group rounded-xl border border-line bg-panel p-4 shadow-panel transition-colors hover:border-line-strong"
    >
      <div className="flex items-center justify-between">
        <p className="text-[12.5px] font-medium text-muted">{label}</p>
        <span className="text-faint transition-colors group-hover:text-accent">{icon}</span>
      </div>
      <p className="num mt-2 text-[28px] font-semibold leading-none tracking-tight" style={accent ? { color: accent } : undefined}>
        {value}
      </p>
      {sub && <p className="mt-1.5 text-[12px] text-muted">{sub}</p>}
      {children && <div className="mt-3">{children}</div>}
    </Link>
  );
}

function SplitRow({ items }: { items: { label: string; value: number; color: string; to: string }[] }) {
  return (
    <div className="grid grid-cols-4 gap-1.5">
      {items.map((i) => (
        <span key={i.label} className="rounded-md bg-panel-2 px-1.5 py-1 text-center">
          <span className="num block text-[13px] font-semibold" style={{ color: i.color }}>
            {i.value}
          </span>
          <span className="block truncate text-[10.5px] text-faint">{i.label}</span>
        </span>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const user = useUser();
  const { data: stats, isLoading } = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 15_000 });
  const { data: system } = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 30_000 });

  if (isLoading || !stats) {
    return (
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-32" />
        ))}
      </div>
    );
  }
  const h = stats.devices.health;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            {greeting}
            {user ? `, ${user}` : ""}
          </h1>
          <p className="mt-0.5 text-[13px] text-muted">
            {stats.testbeds.free} of {stats.testbeds.total} testbeds free · {h.offline} offline · {h.dormant} dormant · {h.unknown} unknown
          </p>
        </div>
        {system?.assistant.available && <button
          onClick={() => window.dispatchEvent(new CustomEvent("lk:assistant"))}
          className="flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-[12.5px] text-muted transition-colors hover:border-accent/50 hover:text-ink"
        >
          <Sparkles className="size-4 text-accent" />
          “Find a free testbed for this afternoon”
        </button>}
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Devices"
          value={stats.devices.total}
          sub={`${stats.devices.switches} switches · ${stats.devices.unracked} not racked`}
          to="/devices"
          icon={<Server className="size-4" />}
        >
          <HealthBar health={h} />
          <div className="mt-2 flex gap-3 text-[11.5px] text-muted">
            <span className="flex items-center gap-1.5">
              <StatusDot status="online" /> {h.online}
            </span>
            <span className="flex items-center gap-1.5">
              <StatusDot status="alerting" /> {h.alerting}
            </span>
            <span className="flex items-center gap-1.5">
              <StatusDot status="offline" /> {h.offline}
            </span>
            <span className="flex items-center gap-1.5">
              <StatusDot status="unknown" /> {h.unknown + h.dormant}
            </span>
          </div>
        </Stat>
        <Stat label="Testbeds" value={stats.testbeds.total} to="/testbeds" icon={<FlaskConical className="size-4" />}>
          <SplitRow
            items={[
              { label: "Free", value: stats.testbeds.free, color: "var(--ok)", to: "/testbeds?status=free" },
              { label: "In use", value: stats.testbeds.in_use, color: "var(--warn)", to: "/testbeds?status=in_use" },
              { label: "Blocked", value: stats.testbeds.blocked, color: "var(--violet)", to: "/testbeds?status=blocked" },
              { label: "Maint.", value: stats.testbeds.maintenance, color: "var(--idle)", to: "/testbeds?status=maintenance" },
            ]}
          />
        </Stat>
        <Stat
          label="Your bookings"
          value={stats.my_bookings.length}
          sub={stats.my_bookings.length ? stats.my_bookings.map((b) => b.testbed_name).join(", ") : "Nothing booked"}
          to="/bookings"
          icon={<CalendarClock className="size-4" />}
          accent={stats.my_bookings.length ? "var(--accent)" : undefined}
        />
        <Stat
          label="Racks & floors"
          value={stats.racks}
          sub={`${stats.floor_plans} floor plan${stats.floor_plans === 1 ? "" : "s"} · ${stats.networks} networks`}
          to={stats.floor_plans ? "/floor" : "/racks"}
          icon={stats.floor_plans ? <MapIcon className="size-4" /> : <Rows3 className="size-4" />}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          title="Your bookings"
          className="xl:col-span-1"
          actions={
            <Link to="/testbeds?status=free" className="text-[12px] text-accent hover:underline">
              Book a testbed
            </Link>
          }
          bodyClassName="divide-y divide-line"
        >
          {stats.my_bookings.length === 0 ? (
            <EmptyState icon={<CalendarClock className="size-5" />} title="No active bookings">
              Pick a free testbed, or ask the assistant to find one with the hardware you need.
            </EmptyState>
          ) : (
            stats.my_bookings.map((b) => (
              <div key={b.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <BookingLine booking={b} />
                <BookingActions booking={b} compact />
              </div>
            ))
          )}
        </Card>

        <Card
          title="In use now"
          subtitle={`${stats.in_use.length} testbed${stats.in_use.length === 1 ? "" : "s"} booked`}
          actions={
            <Link to="/bookings" className="flex items-center gap-1 text-[12px] text-accent hover:underline">
              Timeline <ArrowRight className="size-3" />
            </Link>
          }
          bodyClassName="divide-y divide-line"
        >
          {stats.in_use.length === 0 ? (
            <EmptyState title="Every testbed is free" />
          ) : (
            stats.in_use.slice(0, 8).map((b) => (
              <div key={b.id} className="px-4 py-2.5">
                <BookingLine booking={b} />
              </div>
            ))
          )}
          {stats.upcoming.length > 0 && (
            <div className="bg-panel-2/50 px-4 py-2.5">
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">Next 24 hours</p>
              {stats.upcoming.slice(0, 4).map((b) => (
                <div key={b.id} className="py-1">
                  <BookingLine booking={b} />
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card
          title="Down"
          subtitle="Offline or dormant devices (alerting counts as up)"
          actions={
            <Link to="/devices?status=offline,dormant" className="text-[12px] text-accent hover:underline">
              All
            </Link>
          }
          bodyClassName="divide-y divide-line"
        >
          {stats.attention.length === 0 ? (
            <EmptyState icon={<CircleAlert className="size-5" />} title="All devices healthy" />
          ) : (
            stats.attention.map((d) => (
              <Link key={d.id} to={`/devices/${d.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-panel-2">
                <StatusDot status={d.status} pulse />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-[12.5px] font-medium">{d.name}</p>
                  <p className="truncate text-[11.5px] text-faint">
                    {d.model ?? roleLabel(d.role)} · {d.status_detail ?? d.status} · {relative(d.status_changed_at)}
                  </p>
                </div>
                {d.in_use_by && <Badge tone="warn">{d.in_use_by}</Badge>}
              </Link>
            ))
          )}
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card
          title="Change log"
          className="xl:col-span-2"
          actions={
            <Link to="/activity" className="text-[12px] text-accent hover:underline">
              View all
            </Link>
          }
          bodyClassName=""
        >
          <ActivityTable events={stats.events} compact />
        </Card>
        <Card title="Data sources" subtitle="Live status providers">
          <ul className="space-y-3">
            {system?.providers
              .filter((p) => p.enabled || p.name !== "demo")
              .map((p) => (
                <li key={p.name} className="flex items-start gap-3">
                  <span
                    className={clsx("mt-1.5 size-2 rounded-full", !p.enabled ? "bg-faint" : p.last_error ? "bg-bad" : "bg-ok")}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] font-medium">{p.label}</p>
                    <p className="truncate text-[11.5px] text-faint">
                      {!p.enabled
                        ? p.disabled_reason
                        : p.last_error
                          ? p.last_error
                          : `${p.device_count} devices · polled ${relative(p.last_ok)}`}
                    </p>
                  </div>
                </li>
              ))}
            {system?.last_import && (
              <li className="flex items-start gap-3 border-t border-line pt-3">
                <span className={clsx("mt-1.5 size-2 rounded-full", system.last_import.invalid ? "bg-warn" : "bg-ok")} />
                <div className="min-w-0 flex-1">
                  <Link to="/import" className="text-[13px] font-medium hover:text-accent">
                    Testbed files
                  </Link>
                  <p className="text-[11.5px] text-faint">
                    {system.last_import.testbeds} testbeds · {system.last_import.devices} devices
                    {system.last_import.invalid ? ` · ${system.last_import.invalid} files with errors` : ""} · {relative(system.last_import.finished_at)}
                  </p>
                </div>
              </li>
            )}
          </ul>
        </Card>
      </div>
    </div>
  );
}
