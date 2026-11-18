import { useQuery } from "@tanstack/react-query";
import { ChevronDown, CloudOff, ExternalLink, KeyRound, Network as NetworkIcon, Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, EmptyState, HealthBar, PageHeader, Skeleton, StatusDot, clsx } from "../components/ui";
import { api } from "../lib/api";
import type { DeviceStatus, Network } from "../lib/types";

const HEALTH_STATES: DeviceStatus[] = ["online", "alerting", "offline", "dormant", "unknown"];

function networkRegion(n: Network): string {
  return n.region ?? (n.provider === "yaml" ? "Testbed groupings" : "Ungrouped");
}

function NetworkHealth({ n }: { n: Network }) {
  if (n.has_key === false) {
    return <p className="flex items-center gap-2 text-[13px] text-warn"><KeyRound aria-hidden className="size-4 shrink-0" />Status unknown: provider access unavailable</p>;
  }
  if (n.device_count === 0) return <p className="text-[13px] text-muted">No imported devices</p>;
  return (
    <div>
      <HealthBar health={n.health} className="mb-2" />
      <dl className="grid grid-cols-5 gap-x-2">
        {HEALTH_STATES.map((status) => (
          <div key={status} className="flex min-w-0 flex-col">
            <dt className="order-2 mt-0.5 text-[11px] capitalize text-muted">{status}</dt>
            <dd className="flex items-center gap-1.5 text-[13px] font-semibold text-ink"><span aria-hidden><StatusDot status={status} /></span><span className="num">{n.health[status]}</span></dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function NetworkRow({ n }: { n: Network }) {
  const [expanded, setExpanded] = useState(false);
  const declaredOnly = n.reported_count === 0 && n.testbeds.length > 0;
  const fromFiles = n.device_count - n.reported_count;
  const externalUrl = n.url ?? (n.dashboard_host ? `https://${n.dashboard_host}` : null);
  const detailsId = `network-details-${n.id}`;
  return (
    <li className="border-t border-line px-4 py-3 first:border-t-0 sm:px-5">
      <div className="grid items-center gap-x-6 gap-y-3 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <Link to={`/devices?network=${n.id}`} className="break-words text-[14px] font-semibold leading-5 hover:text-accent">{n.name}</Link>
          <p className="mt-1 text-xs text-muted"><span className="num font-medium text-ink">{n.device_count}</span> imported devices</p>
          {n.testbeds.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs">
              {n.testbeds.map((t) => <Link key={t.id} to={`/testbeds/${t.id}`} className="break-all text-accent hover:underline">{t.name}</Link>)}
            </div>
          )}
          {n.has_key === false ? (
            <p className="mt-1 flex items-start gap-1.5 text-xs text-warn"><KeyRound aria-hidden className="mt-0.5 size-3 shrink-0" />Provider credential is not configured.</p>
          ) : declaredOnly ? (
            <p className="mt-1 flex items-start gap-1.5 text-xs text-muted"><CloudOff aria-hidden className="mt-0.5 size-3 shrink-0" />Declared in files; no devices reported in this network.</p>
          ) : null}
        </div>
        <div className="min-w-0"><NetworkHealth n={n} /></div>
        <div className="flex flex-wrap items-center gap-2 lg:w-52 lg:flex-nowrap lg:justify-end">
          {externalUrl && (
            <a href={externalUrl} target="_blank" rel="noreferrer noopener" aria-label={`Open provider console for ${n.name}`} className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2 text-xs font-medium text-accent hover:bg-accent-soft">
              Open console <ExternalLink aria-hidden className="size-3.5" />
            </a>
          )}
          <button type="button" aria-expanded={expanded} aria-controls={detailsId} aria-label={`${expanded ? "Hide" : "Show"} connection details for ${n.name}`} onClick={() => setExpanded(!expanded)} className="inline-flex min-h-11 items-center gap-1 rounded-lg px-2 text-xs font-medium text-muted hover:bg-panel-2 hover:text-ink">
            Details <ChevronDown aria-hidden className={clsx("size-3.5 transition-transform", expanded && "rotate-180")} />
          </button>
        </div>
      </div>
      {expanded && (
        <div id={detailsId} className="mt-3 rounded-lg border border-line bg-panel-2 px-3 py-3">
          <dl className="grid gap-x-5 gap-y-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
            <div className="min-w-0"><dt className="text-muted">Network key</dt><dd className="mt-1 break-all font-mono">{n.key}</dd></div>
            <div className="min-w-0"><dt className="text-muted">Organization</dt><dd className="mt-1 break-all font-mono">{n.org_id ?? "Not provided"}</dd></div>
            <div className="min-w-0"><dt className="text-muted">API host</dt><dd className="mt-1 break-all font-mono">{n.api_host ?? "Not provided"}</dd></div>
            <div><dt className="text-muted">Source</dt><dd className="mt-1"><Badge>{n.provider}</Badge></dd></div>
          </dl>
          <p className="mt-3 text-xs text-muted">{n.reported_count} imported devices reported in this network{fromFiles > 0 ? ` · ${fromFiles} additional devices from testbed files` : ""}.</p>
          {n.has_key === false && <p className="mt-2 break-words text-xs text-warn">Configure this provider's credential on the server{n.key_env ? <> using <code className="break-all font-mono">{n.key_env}</code></> : null}.</p>}
          {n.product_types.length > 0 && <div className="mt-2 flex flex-wrap items-center gap-1.5"><span className="mr-1 text-xs text-muted">Network products</span>{n.product_types.map((p) => <Badge key={p}>{p}</Badge>)}</div>}
        </div>
      )}
    </li>
  );
}

export default function Networks() {
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ["networks"], queryFn: api.networks, refetchInterval: 30_000 });
  const groups = new Map<string, Network[]>();
  const allRegions = new Set<string>();
  const term = query.trim().toLocaleLowerCase();
  for (const n of data ?? []) {
    const region = networkRegion(n);
    allRegions.add(region);
    if (group && region !== group) continue;
    const haystack = [n.name, n.key, n.org_id, n.api_host, n.provider, region, ...n.product_types, ...n.testbeds.map((t) => t.name)].join(" ").toLocaleLowerCase();
    if (term && !haystack.includes(term)) continue;
    groups.set(region, [...(groups.get(region) ?? []), n]);
  }
  const sortRegions = (a: string, b: string) => Number(a === "Ungrouped") - Number(b === "Ungrouped") || a.localeCompare(b);
  const ordered = [...groups].sort(([a], [b]) => sortRegions(a, b));
  const matched = [...groups.values()].reduce((total, nets) => total + nets.length, 0);
  return (
    <div>
      <PageHeader title="Networks" subtitle="Browse your lab networks, device health, and linked testbeds." icon={<NetworkIcon className="size-5" />} />
      {isLoading ? <Skeleton className="h-64" /> : isError && !data ? (
        <Card><EmptyState icon={<CloudOff className="size-5" />} title="Networks could not be loaded">Check the connection and try again.<div className="mt-3"><Button onClick={() => void refetch()}>Retry</Button></div></EmptyState></Card>
      ) : !data?.length ? (
        <Card><EmptyState icon={<NetworkIcon className="size-5" />} title="No networks yet">Networks appear when a status provider reports them or a testbed file declares one.</EmptyState></Card>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-end gap-3">
            <label className="w-full min-w-0 flex-none sm:max-w-sm sm:flex-1"><span className="mb-1 block text-xs font-medium text-muted">Find a network</span><span className="flex h-11 items-center gap-2 rounded-lg border border-line bg-panel px-3 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/20"><Search aria-hidden className="size-4 shrink-0 text-muted" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Network, testbed or organization" className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted" /></span></label>
            <label className="min-w-40"><span className="mb-1 block text-xs font-medium text-muted">Region / group</span><select value={group} onChange={(e) => setGroup(e.target.value)} className="h-11 w-full rounded-lg border border-line bg-panel px-3 text-[13px]"><option value="">All groups</option>{[...allRegions].sort(sortRegions).map((r) => <option key={r} value={r}>{r}</option>)}</select></label>
            <p className="pb-3 text-xs text-muted" role="status"><span className="num font-medium text-ink">{matched}</span> of {data.length} networks</p>
          </div>
          <p className="mb-3 max-w-4xl text-xs leading-relaxed text-muted">Health describes imported physical devices reported by configured providers. Shared testbeds can repeat the same devices; network counts are not a unique-device total.</p>
          {isError && <p role="alert" className="mb-3 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-[13px] text-warn">Refresh failed. Showing the last loaded network snapshot. <button type="button" className="ml-1 min-h-8 underline" onClick={() => void refetch()}>Retry</button></p>}
          {matched === 0 ? (
            <Card><EmptyState icon={<Search className="size-5" />} title="No matching networks">Try another name, testbed or group.<div className="mt-3"><Button onClick={() => { setQuery(""); setGroup(""); }}>Clear filters</Button></div></EmptyState></Card>
          ) : (
            <div className="overflow-hidden rounded-xl border border-line bg-panel shadow-panel">
              <div className="hidden grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)_auto] gap-6 border-b border-line px-5 py-2 text-xs font-medium text-muted lg:grid"><span>Network / testbeds</span><span>Imported device health</span><span className="w-52 text-right">Access</span></div>
              {ordered.map(([region, nets]) => (
                <section key={region} aria-label={region}>
                  <h2 className="flex items-center gap-2 border-y border-line bg-panel-2 px-4 py-2 text-xs font-semibold first:border-t-0 sm:px-5"><span>{region}</span><span className="num rounded-full bg-panel-3 px-1.5 py-0.5 text-[11px] font-medium text-muted">{nets.length}</span></h2>
                  <ul>{nets.sort((a, b) => a.name.localeCompare(b.name)).map((n) => <NetworkRow key={n.id} n={n} />)}</ul>
                </section>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
