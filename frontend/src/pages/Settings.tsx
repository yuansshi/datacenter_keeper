import { useQuery } from "@tanstack/react-query";
import { Check, Copy, Moon, Plug, Settings as SettingsIcon, Sparkles, Sun, User } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, Field, Input, PageHeader, Segmented, clsx, useToast } from "../components/ui";
import { api } from "../lib/api";
import { relative } from "../lib/format";
import { setTheme, setUser, useTheme, useUser } from "../lib/user";

function CopyBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-lg border border-line bg-bg p-3 pr-10 font-mono text-[11.5px] text-muted">{text}</pre>
      <button
        className="absolute right-2 top-2 rounded-md p-1 text-faint hover:bg-panel-2 hover:text-ink"
        onClick={() => {
          navigator.clipboard?.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
        aria-label="Copy"
      >
        {copied ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}
      </button>
    </div>
  );
}

export default function Settings() {
  const user = useUser();
  const theme = useTheme();
  const toast = useToast();
  const [name, setName] = useState(user);
  const { data: system } = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 15_000 });
  const origin = window.location.origin;

  return (
    <div className="space-y-4">
      <PageHeader title="Settings" icon={<SettingsIcon className="size-5" />} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="You" subtitle="Bookings and changes are recorded under this name">
          <div className="flex items-end gap-2">
            <Field label="Name">
              <Input value={name} onChange={(e) => setName(e.target.value)} icon={<User className="size-4" />} className="w-64" />
            </Field>
            <Button
              variant="primary"
              disabled={!name.trim() || name.trim() === user}
              onClick={() => {
                setUser(name);
                toast({ tone: "ok", title: `Hi, ${name.trim()}` });
              }}
            >
              Save
            </Button>
          </div>
          <p className="mt-3 text-[12px] text-faint">
            For real sign-in, run Labkeeper behind your SSO proxy and set <span className="font-mono">auth.user_header</span> to the header it adds.
          </p>
          <div className="mt-4 flex items-center gap-3">
            <span className="text-[12.5px] text-muted">Theme</span>
            <Segmented
              value={theme}
              onChange={setTheme}
              options={[
                { value: "dark", label: <span className="flex items-center gap-1"><Moon className="size-3.5" />Dark</span> },
                { value: "light", label: <span className="flex items-center gap-1"><Sun className="size-3.5" />Light</span> },
              ]}
            />
          </div>
        </Card>

        <Card title="Data sources">
          <ul className="space-y-3">
            {system?.providers.map((p) => (
              <li key={p.name} className="flex items-start gap-3">
                <span className={clsx("mt-1.5 size-2 rounded-full", !p.enabled ? "bg-faint" : p.last_error ? "bg-bad" : "bg-ok")} />
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 text-[13px] font-medium">
                    {p.label}
                    <Badge>{p.name}</Badge>
                  </p>
                  <p className="text-[12px] text-muted">
                    {!p.enabled
                      ? p.disabled_reason
                      : p.last_error
                        ? p.last_error
                        : `${p.device_count} devices · last poll ${relative(p.last_ok)}${p.last_duration_ms != null ? ` (${p.last_duration_ms} ms)` : ""}`}
                  </p>
                  {p.regions && p.regions.length > 1 && (
                    <ul className="mt-2 space-y-1 rounded-lg border border-line bg-panel-2 p-2">
                      {p.regions.map((r) => (
                        <li key={r.host} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px]">
                          <span className={clsx("size-1.5 rounded-full", !r.has_key ? "bg-faint" : r.error ? "bg-bad" : "bg-ok")} />
                          <span className="font-medium">{r.name}</span>
                          <span className="font-mono text-[11px] text-faint">{r.host}</span>
                          <span className="flex-1" />
                          {r.has_key ? (
                            <span className={r.error ? "text-bad" : "text-muted"}>
                              {r.error ?? `${r.devices} device${r.devices === 1 ? "" : "s"} · ${r.orgs ?? 0} org${r.orgs === 1 ? "" : "s"}`}
                            </span>
                          ) : (
                            <span className="text-faint">
                              no key{r.key_env ? (
                                <>
                                  {" "}
                                  · set <span className="font-mono">{r.key_env}</span>
                                </>
                              ) : null}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[12px] text-faint">
            Inventory and booking work without a cloud account. Enable TCP reachability for management endpoints and use the console checks
            defined by your testbed files. Optional provider adapters can add richer status and inventory; see docs/PLUGINS.md.
          </p>
        </Card>

        <Card title="Assistant" subtitle={system?.assistant.model}>
          <p className="flex items-center gap-2 text-[13px]">
            <Sparkles className="size-4 text-accent" />
            {system?.assistant.available ? (
              <span className="text-ok">Ready</span>
            ) : (
              <span className="text-warn">{system?.assistant.reason ?? "Checking…"}</span>
            )}
          </p>
          <p className="mt-2 text-[12px] text-muted">
            The assistant uses Claude with Labkeeper's own tools: it can look things up, book and release testbeds, test consoles, place devices
            and build floor plans from images. Every action it takes is in the activity log, tagged “via agent”.
          </p>
        </Card>

        <Card title="Use from Claude Code or Claude Desktop" subtitle="Labkeeper is also an MCP server">
          <p className="mb-2 text-[12.5px] text-muted">Remote (this server):</p>
          <CopyBlock text={`claude mcp add --transport http labkeeper ${origin}/mcp/ --header "X-Labkeeper-User: ${user || "your-name"}"`} />
          <p className="mb-2 mt-3 text-[12.5px] text-muted">Local (stdio, same database):</p>
          <CopyBlock text={`LABKEEPER_USER=${user || "your-name"} labkeeper mcp -c labkeeper.yaml`} />
          <p className="mt-3 flex items-center gap-1.5 text-[11.5px] text-faint">
            <Plug className="size-3.5" /> 20 tools: lab_overview, find_available_testbeds, book_testbed, test_console, get_switch_ports…
          </p>
        </Card>
      </div>
    </div>
  );
}
