import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronRight, CircleSlash, Clock, KeyRound, Plug, Terminal, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "../lib/api";
import type { ConsoleResult, DeviceDetail } from "../lib/types";
import { Badge, Button, clsx, useToast } from "./ui";

const STATE: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "info"; icon: ReactNode }> = {
  ready: { label: "Prompt", tone: "ok", icon: <CheckCircle2 className="size-4" /> },
  login: { label: "Login prompt", tone: "ok", icon: <KeyRound className="size-4" /> },
  password: { label: "Password prompt", tone: "ok", icon: <KeyRound className="size-4" /> },
  ssh: { label: "SSH answered", tone: "ok", icon: <CheckCircle2 className="size-4" /> },
  connected: { label: "Connected, silent", tone: "warn", icon: <Plug className="size-4" /> },
  busy: { label: "Line busy", tone: "warn", icon: <CircleSlash className="size-4" /> },
  refused: { label: "Refused", tone: "bad", icon: <CircleSlash className="size-4" /> },
  timeout: { label: "Timeout", tone: "bad", icon: <Clock className="size-4" /> },
  proxy_failed: { label: "Jump host failed", tone: "bad", icon: <TriangleAlert className="size-4" /> },
  error: { label: "Error", tone: "bad", icon: <TriangleAlert className="size-4" /> },
};

const TONE_COLOR = { ok: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)", info: "var(--info)" };

function ResultRow({ r }: { r: ConsoleResult }) {
  const [open, setOpen] = useState(false);
  const s = STATE[r.state] ?? STATE.error;
  return (
    <div className="rounded-lg border border-line bg-panel-2">
      <button className="flex w-full items-start gap-2.5 px-3 py-2 text-left" onClick={() => setOpen((v) => !v)}>
        <span className="mt-0.5" style={{ color: TONE_COLOR[s.tone] }}>
          {s.icon}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[12.5px] font-medium">
            {s.label} <span className="font-normal text-muted">· {r.message}</span>
          </p>
          <p className="text-[11px] text-faint">
            {r.protocol} {r.endpoint}
            {r.via ? ` via ${r.via}` : ""} · {r.duration_ms} ms
          </p>
          {r.hints.map((h) => (
            <p key={h} className="text-[11px] text-muted">
              {h}
            </p>
          ))}
        </div>
        {r.output && (open ? <ChevronDown className="size-4 text-faint" /> : <ChevronRight className="size-4 text-faint" />)}
      </button>
      {open && r.output && (
        <pre className="max-h-48 overflow-auto border-t border-line bg-bg px-3 py-2 font-mono text-[11px] leading-relaxed text-muted">{r.output}</pre>
      )}
    </div>
  );
}

export function ConsolePanel({ device }: { device: DeviceDetail }) {
  const toast = useToast();
  const [results, setResults] = useState<Record<string, ConsoleResult>>({});
  const consoles = Object.entries(device.connections).filter(([, c]) => c.is_console);
  const others = Object.entries(device.connections).filter(([, c]) => !c.is_console);
  const test = useMutation({
    mutationFn: (conn?: string) => api.consoleTest(device.id, conn),
    onSuccess: (res) => {
      setResults((cur) => ({ ...cur, ...Object.fromEntries(res.results.map((r) => [r.connection, r])) }));
      const ok = res.results.filter((r) => r.ok).length;
      toast({ tone: ok === res.results.length ? "ok" : "bad", title: `Console test: ${ok}/${res.results.length} reachable` });
    },
    onError: (e) => toast({ tone: "bad", title: "Console test failed", body: (e as Error).message }),
  });
  const pending = test.isPending ? (test.variables ?? "__all") : null;

  return (
    <div className="space-y-3">
      {consoles.length === 0 ? (
        <p className="text-[13px] text-muted">No console connection in the testbed file.</p>
      ) : (
        <>
          <div className="flex items-center justify-between">
            <p className="text-[12px] text-muted">
              Opens each line briefly, sends Enter, and reports what answered. The line is closed straight away.
            </p>
            {consoles.length > 1 && (
              <Button size="xs" icon={<Terminal className="size-3.5" />} loading={pending === "__all"} onClick={() => test.mutate(undefined)}>
                Test all
              </Button>
            )}
          </div>
          {consoles.map(([name, c]) => (
            <div key={name} className="space-y-2">
              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-[12.5px] font-medium">{name}</p>
                  <p className="truncate text-[11.5px] text-faint">
                    {c.protocol} {c.ip}
                    {c.port ? `:${c.port}` : ""}
                    {c.proxy ? ` via ${c.proxy}` : ""}
                    {c.console_server_ip ? ` · terminal server ${c.console_server_ip}` : ""}
                    {c.member ? ` · member ${c.member}` : ""}
                  </p>
                </div>
                <Button size="xs" variant="primary" icon={<Terminal className="size-3.5" />} loading={pending === name} onClick={() => test.mutate(name)}>
                  Test console
                </Button>
              </div>
              {results[name] && <ResultRow r={results[name]} />}
            </div>
          ))}
        </>
      )}
      {others.length > 0 && (
        <div className="border-t border-line pt-3">
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">Other connections</p>
          <div className="flex flex-wrap gap-1.5">
            {others.map(([name, c]) => (
              <Badge key={name} className={clsx("font-mono")} title={c.proxy ? `via ${c.proxy}` : undefined}>
                {name}: {c.protocol} {c.ip}
                {c.port ? `:${c.port}` : ""}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
