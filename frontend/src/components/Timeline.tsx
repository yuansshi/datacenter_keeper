import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { ownerHue, TESTBED_COLOR, when } from "../lib/format";
import type { Booking, TestbedStatus } from "../lib/types";
import { useUser } from "../lib/user";
import { Button, Segmented, clsx } from "./ui";

type Range = "24h" | "3d" | "7d";
const RANGE_HOURS: Record<Range, number> = { "24h": 24, "3d": 72, "7d": 168 };
const LABEL_W = 168;

export function Timeline({
  testbeds,
  onCreate,
  highlightId,
}: {
  testbeds: { id: number; name: string; status: TestbedStatus }[];
  onCreate?: (testbedId: number, at: Date) => void;
  highlightId?: number;
}) {
  const user = useUser();
  const [range, setRange] = useState<Range>("3d");
  const [offsetDays, setOffsetDays] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [hover, setHover] = useState<{ booking: Booking; x: number; y: number } | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  const start = useMemo(() => {
    const d = new Date(now);
    d.setMinutes(0, 0, 0);
    d.setHours(d.getHours() - (range === "24h" ? 2 : 6));
    d.setDate(d.getDate() + offsetDays);
    return d.getTime();
  }, [now, range, offsetDays]);
  const hours = RANGE_HOURS[range];
  const end = start + hours * 3_600_000;
  const trackW = Math.max(width - LABEL_W, 300);
  const x = (t: number) => ((t - start) / (end - start)) * trackW;

  const { data: bookings } = useQuery({
    queryKey: ["bookings", "range", start, end],
    queryFn: () => api.bookings({ start: new Date(start).toISOString(), end: new Date(end).toISOString() }),
    refetchInterval: 20_000,
  });
  const byTestbed = useMemo(() => {
    const m = new Map<number, Booking[]>();
    bookings?.forEach((b) => m.set(b.testbed_id, [...(m.get(b.testbed_id) ?? []), b]));
    return m;
  }, [bookings]);

  const ticks = useMemo(() => {
    const step = range === "24h" ? 2 : range === "3d" ? 6 : 12;
    const out: { t: number; label: string; day: boolean }[] = [];
    const first = new Date(start);
    first.setMinutes(0, 0, 0);
    for (let t = first.getTime(); t <= end; t += 3_600_000) {
      const d = new Date(t);
      if (d.getHours() % step !== 0) continue;
      const day = d.getHours() === 0;
      out.push({
        t,
        day,
        label: day ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) : d.toLocaleTimeString(undefined, { hour: "numeric" }),
      });
    }
    return out;
  }, [start, end, range]);

  const clickTrack = (tbId: number, e: React.MouseEvent<HTMLDivElement>) => {
    if (!onCreate) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const t = start + ((e.clientX - rect.left) / rect.width) * (end - start);
    const rounded = Math.round(t / 1_800_000) * 1_800_000;
    onCreate(tbId, new Date(Math.max(rounded, Date.now())));
  };

  return (
    <div ref={wrap} className="relative">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <div className="flex items-center gap-1">
          <Button size="xs" variant="ghost" onClick={() => setOffsetDays((o) => o - 1)} aria-label="Earlier">
            <ChevronLeft className="size-4" />
          </Button>
          <Button size="xs" variant="ghost" onClick={() => setOffsetDays(0)} disabled={offsetDays === 0}>
            Now
          </Button>
          <Button size="xs" variant="ghost" onClick={() => setOffsetDays((o) => o + 1)} aria-label="Later">
            <ChevronRight className="size-4" />
          </Button>
        </div>
        {onCreate && <p className="hidden text-[11.5px] text-faint md:block">Click an empty slot to book it</p>}
        <Segmented size="xs" value={range} onChange={setRange} options={[{ value: "24h", label: "24 h" }, { value: "3d", label: "3 days" }, { value: "7d", label: "Week" }]} />
      </div>

      <div className="relative overflow-x-hidden">
        <div className="flex h-8 border-b border-line text-[10.5px] text-faint">
          <div style={{ width: LABEL_W }} className="shrink-0 px-4 py-2 font-semibold uppercase tracking-wider">
            Testbed
          </div>
          <div className="relative flex-1">
            {ticks.map((tk) => (
              <span key={tk.t} className={clsx("absolute top-2 -translate-x-1/2 whitespace-nowrap", tk.day && "font-semibold text-muted")} style={{ left: x(tk.t) }}>
                {tk.label}
              </span>
            ))}
          </div>
        </div>
        <div className="relative max-h-[560px] overflow-y-auto">
          {testbeds.map((tb) => (
            <div key={tb.id} className={clsx("flex h-10 border-b border-line last:border-0", tb.id === highlightId && "bg-accent-soft/40")}>
              <div style={{ width: LABEL_W }} className="flex shrink-0 items-center gap-2 truncate px-4">
                <span className="size-1.5 shrink-0 rounded-full" style={{ background: TESTBED_COLOR[tb.status] }} />
                <Link to={`/testbeds/${tb.id}`} className="truncate font-mono text-[12px] hover:text-accent">
                  {tb.name}
                </Link>
              </div>
              <div className={clsx("relative flex-1", onCreate && "cursor-copy")} onClick={(e) => clickTrack(tb.id, e)}>
                {ticks.map((tk) => (
                  <span key={tk.t} className={clsx("absolute inset-y-0 w-px", tk.day ? "bg-line-strong" : "bg-line/60")} style={{ left: x(tk.t) }} />
                ))}
                {(byTestbed.get(tb.id) ?? []).map((b) => {
                  const s = Math.max(new Date(b.starts_at).getTime(), start);
                  const e = Math.min(new Date(b.effective_end).getTime(), end);
                  if (e <= s) return null;
                  const mine = b.owner === user;
                  const hue = ownerHue(b.owner);
                  const past = new Date(b.effective_end).getTime() < now;
                  return (
                    <div
                      key={b.id}
                      onClick={(ev) => ev.stopPropagation()}
                      onMouseEnter={(ev) => setHover({ booking: b, x: ev.clientX, y: ev.clientY })}
                      onMouseLeave={() => setHover(null)}
                      className={clsx(
                        "absolute top-1.5 flex h-7 items-center overflow-hidden rounded-md border px-2 text-[11px] font-medium",
                        past && "opacity-45",
                      )}
                      style={{
                        left: x(s),
                        width: Math.max(x(e) - x(s), 4),
                        background: mine ? "var(--accent)" : `hsl(${hue} 55% 45% / 0.22)`,
                        borderColor: mine ? "var(--accent-strong)" : `hsl(${hue} 55% 55% / 0.55)`,
                        color: mine ? "var(--accent-ink)" : "var(--ink)",
                      }}
                    >
                      <span className="truncate">
                        {b.owner}
                        {b.purpose ? ` · ${b.purpose}` : ""}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          {now >= start && now <= end && (
            <div className="pointer-events-none absolute inset-y-0 w-px bg-bad" style={{ left: LABEL_W + x(now) }}>
              <span className="absolute -top-0 left-1 rounded bg-bad px-1 text-[9px] font-semibold text-white">now</span>
            </div>
          )}
        </div>
      </div>
      {hover && (
        <div
          className="pointer-events-none fixed z-50 w-64 rounded-lg border border-line bg-panel px-3 py-2 text-[12px] shadow-2xl"
          style={{ left: Math.min(hover.x + 12, window.innerWidth - 270), top: hover.y + 12 }}
        >
          <p className="font-mono font-semibold">{hover.booking.testbed_name}</p>
          <p className="text-muted">
            {hover.booking.owner} · {when(hover.booking.starts_at)} → {when(hover.booking.effective_end)}
          </p>
          {hover.booking.purpose && <p className="mt-0.5 text-faint">{hover.booking.purpose}</p>}
          <p className="mt-0.5 text-[11px] capitalize text-faint">
            {hover.booking.state} · via {hover.booking.created_via}
          </p>
        </div>
      )}
    </div>
  );
}
