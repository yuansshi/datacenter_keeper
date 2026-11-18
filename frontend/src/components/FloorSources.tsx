import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, Check, Copy, Download, PlugZap, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useUser } from "../lib/user";
import type { FloorSource } from "../lib/types";
import { Badge, Button, Card, Input, Select, Spinner, clsx, useToast } from "./ui";

/** Draggable bookmarklet. React refuses javascript: URLs in JSX, so the href is set on the DOM node. */
function BookmarkletLink({ href, title }: { href: string; title: string }) {
  const ref = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    ref.current?.setAttribute("href", href);
  }, [href]);
  return (
    <a
      ref={ref}
      onClick={(e) => e.preventDefault()}
      draggable
      title="Drag me to your bookmarks bar"
      className="inline-flex h-8 cursor-grab items-center gap-1.5 rounded-lg border border-accent/60 bg-accent-soft px-3 text-[13px] font-medium text-accent active:cursor-grabbing"
    >
      <Bookmark className="size-4" />
      {title}
    </a>
  );
}

function SitePicker({ source }: { source: FloorSource }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [site, setSite] = useState("");
  const { data: sites, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["floorsource-sites", source.name],
    queryFn: () => api.floorSourceSites(source.name),
    staleTime: 5 * 60_000,
  });
  const filtered = useMemo(
    () => (sites ?? []).filter((s) => !q || `${s.name} ${s.group ?? ""} ${s.detail ?? ""}`.toLowerCase().includes(q.toLowerCase())).slice(0, 300),
    [sites, q],
  );
  const importIt = useMutation({
    mutationFn: () => api.importFromSource(source.name, site),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["floorplans"] });
      toast({ tone: "ok", title: r.created ? `Imported ${r.name}` : `Refreshed ${r.name}`, body: `${r.changes} cell changes` });
      navigate(`/floor/${r.id}`);
    },
    onError: (e) => toast({ tone: "bad", title: `Could not import from ${source.label}`, body: (e as Error).message }),
  });
  if (isLoading) return <p className="flex items-center gap-2 text-[12.5px] text-muted"><Spinner className="size-3.5" /> Loading sites…</p>;
  if (error) return <p className="text-[12.5px] text-bad">{(error as Error).message}</p>;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input placeholder="Filter sites" value={q} onChange={(e) => setQ(e.target.value)} className="w-44" />
      <Select value={site} onChange={(e) => setSite(e.target.value)} className="min-w-0 max-w-[320px] flex-1">
        <option value="">Choose a site ({filtered.length})</option>
        {filtered.map((s) => (
          <option key={s.id} value={s.id}>
            {s.group ? `${s.group} · ` : ""}
            {s.name}
            {s.detail ? ` (${s.detail})` : ""}
          </option>
        ))}
      </Select>
      <Button variant="primary" disabled={!site} loading={importIt.isPending} icon={<Download className="size-4" />} onClick={() => importIt.mutate()}>
        Import
      </Button>
      <Button variant="ghost" size="sm" onClick={() => refetch()} aria-label="Reload sites" icon={isFetching ? <Spinner className="size-3.5" /> : <RefreshCw className="size-3.5" />} />
    </div>
  );
}

/** Fill the placeholders a source's bookmarklet leaves for us: our own URL and the user's name. */
function fill(code: string, user: string, encoded: boolean): string {
  const lk = JSON.stringify(window.location.origin);
  const u = JSON.stringify(user || "");
  const enc = (v: string) => (encoded ? encodeURIComponent(v) : v);
  return code.split("__LABKEEPER_URL__").join(enc(lk)).split("__LABKEEPER_USER__").join(enc(u));
}

function SourceRow({ source }: { source: FloorSource }) {
  const [copied, setCopied] = useState(false);
  const user = useUser();
  const exp = source.browser_export;
  return (
    <div className="space-y-3 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <PlugZap className="size-4 text-accent" />
        <span className="text-[13.5px] font-semibold">{source.label}</span>
        <Badge tone={source.can_sync ? "ok" : "neutral"}>{source.can_sync ? `live sync every ${Math.round(source.sync_interval / 60)} min` : "browser export"}</Badge>
      </div>
      {source.can_sync ? (
        <SitePicker source={source} />
      ) : (
        source.sync_disabled_reason && <p className="text-[12px] text-faint">{source.sync_disabled_reason}</p>
      )}
      {exp && (
        <div className="rounded-lg border border-line bg-panel-2/60 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <BookmarkletLink href={fill(exp.bookmarklet, user, true)} title={exp.title} />
            <Button
              size="sm"
              variant="ghost"
              icon={copied ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}
              onClick={() => {
                navigator.clipboard?.writeText(fill(exp.snippet, user, false));
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              Copy console snippet
            </Button>
          </div>
          <p className="mt-2 text-[12px] text-muted">{exp.instructions}</p>
          {exp.url_hint && <p className={clsx("mt-1 break-all font-mono text-[11px] text-faint")}>{exp.url_hint}</p>}
        </div>
      )}
    </div>
  );
}

export function FloorSourcesCard() {
  const { data: sources } = useQuery({ queryKey: ["floorsources"], queryFn: api.floorSources, staleTime: 60_000 });
  if (!sources?.length) return null;
  return (
    <Card title="Connected sources" subtitle="Keep floor plans in sync with your DCIM / lab tracking system" bodyClassName="divide-y divide-line p-4">
      {sources.map((s) => (
        <SourceRow key={s.name} source={s} />
      ))}
    </Card>
  );
}
