import { useMutation } from "@tanstack/react-query";
import { ArrowLeftRight, Copy, ExternalLink, Gauge, Terminal } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { ConsoleLine, ConsoleResult, TrafficGenerator } from "../lib/types";
import { Badge, Button, Card, clsx, useToast } from "./ui";

const RESULT_TONE: Record<string, "ok" | "warn" | "bad"> = {
  ready: "ok", login: "ok", password: "ok", ssh: "ok", connected: "warn", busy: "warn",
  refused: "bad", timeout: "bad", proxy_failed: "bad", error: "bad",
};
const RESULT_LABEL: Record<string, string> = {
  ready: "prompt", login: "login prompt", password: "password prompt", ssh: "SSH answered", connected: "connected, silent",
  busy: "line busy", refused: "refused", timeout: "timeout", proxy_failed: "jump host failed", error: "error",
};

function command(c: Pick<ConsoleLine, "protocol" | "host" | "port">): string | null {
  if (!c.host) return null;
  if (c.protocol === "ssh") return `ssh ${c.host}${c.port && c.port !== 22 ? ` -p ${c.port}` : ""}`;
  return `${c.protocol === "telnet" || !c.protocol ? "telnet" : c.protocol} ${c.host}${c.port ? ` ${c.port}` : ""}`;
}

function ResultBadge({ r }: { r?: ConsoleResult }) {
  if (!r) return null;
  return (
    <Badge tone={RESULT_TONE[r.state] ?? "bad"} title={`${r.message} · ${r.duration_ms} ms`}>
      {RESULT_LABEL[r.state] ?? r.state}
    </Badge>
  );
}

function useLineTest() {
  const toast = useToast();
  const [results, setResults] = useState<Record<string, ConsoleResult>>({});
  const test = useMutation({
    mutationFn: async (lines: { device_id: number; connection: string }[]) => {
      const out = await Promise.all(lines.map((l) => api.consoleTest(l.device_id, l.connection).then((r) => ({ l, r }))));
      return out;
    },
    onSuccess: (out) => {
      const next: Record<string, ConsoleResult> = {};
      out.forEach(({ l, r }) => r.results.forEach((x) => (next[`${l.device_id}/${x.connection}`] = x)));
      setResults((cur) => ({ ...cur, ...next }));
      const all = Object.values(next);
      if (all.length > 1) {
        const ok = all.filter((x) => x.ok).length;
        toast({ tone: ok === all.length ? "ok" : "bad", title: `${ok}/${all.length} lines answered` });
      }
    },
    onError: (e) => toast({ tone: "bad", title: "Test failed", body: (e as Error).message }),
  });
  return { results, test };
}

function CopyButton({ text }: { text: string }) {
  const toast = useToast();
  return (
    <button
      className="rounded p-1 text-faint hover:bg-panel-3 hover:text-ink"
      title={`Copy: ${text}`}
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => toast({ tone: "ok", title: "Copied", body: text }));
      }}
    >
      <Copy className="size-3.5" />
    </button>
  );
}

/** Every console line of a testbed, with one-click tests (each opens the line, sends Enter, closes it). */
export function ConsoleLines({ lines, total }: { lines: ConsoleLine[]; total: number }) {
  const { results, test } = useLineTest();
  const pending = test.isPending ? test.variables : null;
  const withoutConsole = total - new Set(lines.map((l) => l.device_id)).size;
  return (
    <Card
      title="Consoles"
      subtitle={`${lines.length} console line${lines.length === 1 ? "" : "s"}${withoutConsole > 0 ? ` · ${withoutConsole} device${withoutConsole === 1 ? " has" : "s have"} none in the testbed files` : ""}`}
      actions={
        lines.length > 1 ? (
          <Button size="xs" icon={<Terminal className="size-3.5" />} loading={!!pending && pending.length > 1} onClick={() => test.mutate(lines)}>
            Test all
          </Button>
        ) : null
      }
      bodyClassName=""
    >
      {lines.length === 0 ? (
        <p className="p-4 text-[13px] text-muted">No console connections in the testbed files.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[12.5px]">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                <th className="py-2 pl-4 pr-2 font-semibold">Device</th>
                <th className="px-2 py-2 font-semibold">Line</th>
                <th className="px-2 py-2 font-semibold">Endpoint</th>
                <th className="hidden px-2 py-2 font-semibold md:table-cell">Via</th>
                <th className="px-2 py-2 pr-4 text-right font-semibold">Check</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((c) => {
                const key = `${c.device_id}/${c.connection}`;
                const cmd = command(c);
                const busy = !!pending && pending.length === 1 && pending[0].device_id === c.device_id && pending[0].connection === c.connection;
                return (
                  <tr key={key} className="border-b border-line last:border-0">
                    <td className="py-2 pl-4 pr-2">
                      <Link to={`/devices/${c.device_id}`} className="font-mono font-medium hover:text-accent">
                        {c.device}
                      </Link>
                      {c.member != null && <span className="ml-1.5 text-[11px] text-faint">member {c.member}</span>}
                    </td>
                    <td className="px-2 font-mono text-[11.5px] text-muted">{c.connection}</td>
                    <td className="px-2">
                      <span className="flex items-center gap-1 font-mono text-[11.5px]">
                        <span className="text-faint">{c.protocol ?? "telnet"}</span> {c.host ?? "—"}
                        {c.port ? <span className="text-accent">:{c.port}</span> : null}
                        {cmd && <CopyButton text={cmd} />}
                      </span>
                    </td>
                    <td className="hidden px-2 font-mono text-[11.5px] text-muted md:table-cell">{c.via ?? (c.console_server && c.console_server !== c.host ? c.console_server : "direct")}</td>
                    <td className="px-2 pr-4">
                      <span className="flex items-center justify-end gap-2">
                        <ResultBadge r={results[key]} />
                        <Button size="xs" variant="ghost" loading={busy} onClick={() => test.mutate([{ device_id: c.device_id, connection: c.connection }])}>
                          Test
                        </Button>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

const DETAIL_LABEL: Record<string, string> = {
  product: "Product",
  client_vm: "Client VM",
  profile: "Profile",
  traffic: "Traffic",
  mutating_execution: "Changes",
};

/** Traffic generators (Ixia, Spirent…): API / client VM, chassis, and which device port each test port is cabled to. */
export function TrafficGenerators({ generators }: { generators: TrafficGenerator[] }) {
  const { results, test } = useLineTest();
  return (
    <>
      {generators.map((g) => {
        const key = `${g.device_id}/${g.api_connection}`;
        const details = Object.entries(g.details).filter(([, v]) => v != null && v !== "");
        return (
          <Card
            key={g.device_id}
            title={
              <span className="flex items-center gap-2">
                <Gauge className="size-4 text-accent" />
                Traffic generator ·{" "}
                <Link to={`/devices/${g.device_id}`} className="font-mono hover:text-accent">
                  {g.name}
                </Link>
              </span>
            }
            subtitle={[String(g.details.product ?? g.type ?? ""), g.os].filter(Boolean).join(" · ")}
            actions={
              <span className="flex items-center gap-2">
                <ResultBadge r={results[key]} />
                {g.api_connection && (
                  <Button size="xs" variant="ghost" loading={test.isPending} onClick={() => test.mutate([{ device_id: g.device_id, connection: g.api_connection! }])}>
                    Test API
                  </Button>
                )}
                {g.api && (
                  <a href={g.api} target="_blank" rel="noreferrer noopener">
                    <Button size="xs" icon={<ExternalLink className="size-3.5" />}>
                      Open API
                    </Button>
                  </a>
                )}
              </span>
            }
            bodyClassName=""
          >
            <div className="grid gap-x-6 gap-y-1.5 border-b border-line px-4 py-3 text-[12.5px] sm:grid-cols-2">
              <Prop label="API / client VM" mono value={g.api} copy />
              <Prop label="Chassis" mono value={g.chassis} copy />
              <Prop label="Session" mono value={g.session} />
              {details.map(([k, v]) => (
                <Prop key={k} label={DETAIL_LABEL[k] ?? k.replace(/_/g, " ")} value={String(v)} mono={k === "profile"} />
              ))}
            </div>
            {g.ports.length === 0 ? (
              <p className="p-4 text-[12.5px] text-muted">No test ports in the testbed topology.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-[12.5px]">
                  <thead>
                    <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                      <th className="py-2 pl-4 pr-2 font-semibold">Test port</th>
                      <th className="px-2 py-2 font-semibold">Tag</th>
                      <th className="hidden px-2 py-2 font-semibold sm:table-cell">Media</th>
                      <th className="px-2 py-2 font-semibold">IPv4</th>
                      <th className="px-2 py-2 pr-4 font-semibold">Cabled to</th>
                    </tr>
                  </thead>
                  <tbody>
                    {g.ports.map((p) => {
                      const [chassis, card, port] = p.interface.split("/");
                      return (
                        <tr key={p.interface} className="border-b border-line last:border-0">
                          <td className="py-2 pl-4 pr-2">
                            <span className="font-mono font-medium">{p.interface}</span>
                            {port && <span className="ml-1.5 text-[11px] text-faint">chassis {chassis} · card {card} · port {port}</span>}
                          </td>
                          <td className="px-2">{p.tag ? <Badge tone="accent" className="font-mono">{p.tag}</Badge> : "—"}</td>
                          <td className="hidden px-2 text-muted sm:table-cell">{p.media ?? "—"}</td>
                          <td className="px-2 font-mono text-[11.5px] text-muted">{p.ipv4 ?? "—"}</td>
                          <td className="px-2 pr-4">
                            {p.peer ? (
                              <span className="flex items-center gap-1.5">
                                <ArrowLeftRight className="size-3.5 text-faint" />
                                {p.peer.device_id ? (
                                  <Link to={`/devices/${p.peer.device_id}`} className="font-mono font-medium hover:text-accent">
                                    {p.peer.device}
                                  </Link>
                                ) : (
                                  <span className="font-mono">{p.peer.device}</span>
                                )}
                                <span className="font-mono text-muted">port {p.peer.interface}</span>
                                {p.peer.tag && <Badge className="font-mono py-0">{p.peer.tag}</Badge>}
                              </span>
                            ) : (
                              <span className="text-faint">not in topology links</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        );
      })}
    </>
  );
}

function Prop({ label, value, mono, copy }: { label: string; value: string | null | undefined; mono?: boolean; copy?: boolean }) {
  return (
    <div className="flex min-w-0 items-baseline gap-3">
      <span className="w-28 shrink-0 text-muted">{label}</span>
      <span className={clsx("flex min-w-0 items-center gap-1", mono && "font-mono text-[12px]")}>
        <span className="truncate" title={value ?? undefined}>{value ?? "—"}</span>
        {copy && value && <CopyButton text={value} />}
      </span>
    </div>
  );
}
