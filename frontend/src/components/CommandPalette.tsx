import { useQuery } from "@tanstack/react-query";
import { CalendarRange, FlaskConical, Map as MapIcon, Network, Rows3, Search, Server, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { Kbd, clsx } from "./ui";

type Item = { key: string; label: string; hint?: string; icon: ReactNode; to?: string; run?: () => void; group: string };

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const term = q.trim();
  const { data } = useQuery({
    queryKey: ["search", term],
    queryFn: () => api.search(term),
    enabled: open && term.length > 0,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  const items = useMemo<Item[]>(() => {
    const out: Item[] = [];
    if (data) {
      data.devices.forEach((d) => out.push({ key: `d${d.id}`, label: d.name, hint: d.hint, icon: <Server className="size-4" />, to: `/devices/${d.id}`, group: "Devices" }));
      data.testbeds.forEach((t) => out.push({ key: `t${t.id}`, label: t.name, hint: t.hint, icon: <FlaskConical className="size-4" />, to: `/testbeds/${t.id}`, group: "Testbeds" }));
      data.racks.forEach((r) => out.push({ key: `r${r.id}`, label: r.name, hint: r.hint, icon: <Rows3 className="size-4" />, to: `/racks/${r.id}`, group: "Racks" }));
      data.networks.forEach((n) => out.push({ key: `n${n.id}`, label: n.name, hint: n.hint, icon: <Network className="size-4" />, to: `/devices?network=${n.id}`, group: "Networks" }));
    }
    if (term) {
      out.push({
        key: "ask",
        label: `Ask Labkeeper: “${term}”`,
        icon: <Sparkles className="size-4" />,
        group: "Assistant",
        run: () => {
          window.dispatchEvent(new CustomEvent("lk:assistant"));
          window.dispatchEvent(new CustomEvent("lk:ask", { detail: term }));
        },
      });
    } else {
      out.push(
        { key: "g-free", label: "Free testbeds", icon: <FlaskConical className="size-4" />, to: "/testbeds?status=free", group: "Jump to" },
        { key: "g-off", label: "Offline devices", icon: <Server className="size-4" />, to: "/devices?status=offline,dormant", group: "Jump to" },
        { key: "g-book", label: "Booking timeline", icon: <CalendarRange className="size-4" />, to: "/bookings", group: "Jump to" },
        { key: "g-floor", label: "Floor plans", icon: <MapIcon className="size-4" />, to: "/floor", group: "Jump to" },
      );
    }
    return out;
  }, [data, term]);

  useEffect(() => setActive(0), [term]);

  if (!open) return null;

  const choose = (item: Item | undefined) => {
    if (!item) return;
    onClose();
    if (item.to) navigate(item.to);
    item.run?.();
  };

  let lastGroup = "";
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-[12vh] backdrop-blur-[2px]" onMouseDown={onClose}>
      <div className="fade-in w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-panel shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b border-line px-4">
          <Search className="size-4 text-faint" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((a) => Math.min(a + 1, items.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => Math.max(a - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                choose(items[active]);
              }
            }}
            placeholder="Device name, serial, IP, model, testbed, rack…"
            className="h-12 flex-1 bg-transparent text-[14px] outline-none placeholder:text-faint"
          />
          <Kbd>esc</Kbd>
        </div>
        <div className="max-h-[50vh] overflow-y-auto p-2">
          {items.length === 0 && <p className="px-3 py-6 text-center text-[13px] text-muted">No matches</p>}
          {items.map((item, i) => {
            const header = item.group !== lastGroup ? item.group : null;
            lastGroup = item.group;
            return (
              <div key={item.key}>
                {header && <p className="px-3 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-wider text-faint">{header}</p>}
                <button
                  onMouseEnter={() => setActive(i)}
                  onClick={() => choose(item)}
                  className={clsx(
                    "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left",
                    i === active ? "bg-accent-soft text-ink" : "text-muted",
                  )}
                >
                  <span className={i === active ? "text-accent" : "text-faint"}>{item.icon}</span>
                  <span className="flex-1 truncate text-[13px] font-medium text-ink">{item.label}</span>
                  {item.hint && <span className="truncate font-mono text-[11px] text-faint">{item.hint}</span>}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>,
    document.body,
  );
}
