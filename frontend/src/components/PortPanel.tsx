import { useState } from "react";
import type { Port } from "../lib/types";
import { clsx } from "./ui";

function portColor(p: Port): string {
  if (!p.enabled || p.status === "Disabled") return "var(--faint)";
  if ((p.errors?.length ?? 0) > 0) return "var(--bad)";
  if ((p.warnings?.length ?? 0) > 0) return "var(--warn)";
  if (p.status === "Connected") return "var(--ok)";
  return "var(--line-strong)";
}

/** A switch faceplate: two rows of ports (odd on top, like most switches), hover for detail. */
export function PortPanel({ ports, legend = true }: { ports: Port[]; legend?: boolean }) {
  const [hover, setHover] = useState<Port | null>(null);
  const numeric = ports.filter((p) => /^\d+$/.test(p.id));
  const modules = ports.filter((p) => !/^\d+$/.test(p.id));
  const top = numeric.filter((p) => Number(p.id) % 2 === 1);
  const bottom = numeric.filter((p) => Number(p.id) % 2 === 0);
  const shown = hover ?? ports.find((p) => p.tags?.length) ?? null;

  const cell = (p: Port) => (
    <button
      key={p.id}
      onMouseEnter={() => setHover(p)}
      onFocus={() => setHover(p)}
      className={clsx(
        "relative flex h-8 w-7 shrink-0 flex-col items-center justify-center rounded-[5px] border text-[9.5px] font-semibold",
        hover?.id === p.id ? "border-accent" : "border-line-strong/60",
      )}
      style={{ background: `color-mix(in srgb, ${portColor(p)} ${p.status === "Connected" ? 30 : 12}%, var(--panel-2))` }}
      aria-label={`Port ${p.id} ${p.status ?? ""}`}
    >
      <span className="num text-muted">{p.id}</span>
      <span className="mt-0.5 h-1 w-3.5 rounded-full" style={{ background: portColor(p) }} />
      {p.poe && <span className="absolute -right-0.5 -top-0.5 size-1.5 rounded-full bg-warn" title="PoE" />}
      {p.tags?.length ? <span className="absolute -bottom-0.5 left-1/2 size-1.5 -translate-x-1/2 rounded-full bg-accent" /> : null}
    </button>
  );

  return (
    <div>
      <div className="overflow-x-auto rounded-xl border border-line bg-panel-3/50 p-3">
        <div className="inline-flex flex-col gap-1.5">
          <div className="flex gap-1">{top.map(cell)}</div>
          <div className="flex gap-1">{bottom.map(cell)}</div>
        </div>
        {modules.length > 0 && (
          <div className="ml-4 mt-2 inline-flex flex-wrap gap-1 border-l border-line pl-4 align-top">
            {modules.map((p) => (
              <button
                key={p.id}
                onMouseEnter={() => setHover(p)}
                className="h-8 rounded-[5px] border border-line-strong/60 px-1.5 font-mono text-[9.5px] text-muted"
                style={{ background: `color-mix(in srgb, ${portColor(p)} 22%, var(--panel-2))` }}
              >
                {p.id}
              </button>
            ))}
          </div>
        )}
      </div>
      {legend && <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-faint">
        <span className="flex items-center gap-1">
          <span className="h-1 w-3 rounded-full bg-ok" /> connected
        </span>
        <span className="flex items-center gap-1">
          <span className="h-1 w-3 rounded-full bg-line-strong" /> down
        </span>
        <span className="flex items-center gap-1">
          <span className="h-1 w-3 rounded-full bg-faint" /> disabled
        </span>
        <span className="flex items-center gap-1">
          <span className="h-1 w-3 rounded-full bg-bad" /> errors
        </span>
        <span className="flex items-center gap-1">
          <span className="size-1.5 rounded-full bg-warn" /> PoE
        </span>
        <span className="flex items-center gap-1">
          <span className="size-1.5 rounded-full bg-accent" /> used by a testbed
        </span>
      </div>}
      {shown && (
        <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 rounded-lg border border-line bg-panel-2 p-3 text-[12px] sm:grid-cols-4">
          <p className="col-span-2 font-semibold sm:col-span-4">
            Port {shown.id}
            {shown.name ? ` · ${shown.name}` : ""}
            {shown.is_uplink && <span className="ml-2 text-accent">uplink</span>}
          </p>
          <span className="text-muted">Status</span>
          <span>{shown.status ?? "—"}</span>
          <span className="text-muted">Speed</span>
          <span>{shown.speed ?? "—"}</span>
          <span className="text-muted">VLAN</span>
          <span>{shown.vlan ?? "—"} {shown.type ? `(${shown.type})` : ""}</span>
          <span className="text-muted">Clients</span>
          <span>{shown.clients ?? "—"}</span>
          {shown.neighbor && (
            <>
              <span className="text-muted">Neighbor</span>
              <span className="truncate">
                {shown.neighbor} {shown.neighbor_port}
              </span>
            </>
          )}
          {shown.tags?.length ? (
            <>
              <span className="text-muted">Testbed tags</span>
              <span className="col-span-1 truncate font-mono text-accent sm:col-span-3">{shown.tags.join(", ")}</span>
            </>
          ) : null}
          {shown.errors?.length ? (
            <>
              <span className="text-muted">Errors</span>
              <span className="text-bad">{shown.errors.join(", ")}</span>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
