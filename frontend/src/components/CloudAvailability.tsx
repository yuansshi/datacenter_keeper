import type { ProviderRegion, StatusCounts, SystemInfo } from "../lib/types";
import { clsx } from "./ui";

const COLUMNS: ReadonlyArray<{ key: keyof StatusCounts; label: string }> = [
  { key: "online", label: "Online" },
  { key: "alerting", label: "Alerting" },
  { key: "offline", label: "Offline" },
  { key: "dormant", label: "Dormant" },
  { key: "unknown", label: "Unknown" },
];

type Reading =
  | { state: "current"; counts: StatusCounts; checkedAt: string }
  | { state: "stale" | "unavailable" | "not-configured" | "pending"; checkedAt: string | null };

const LABELS: Record<Reading["state"], string> = {
  current: "Current",
  stale: "Stale · current reading unknown",
  unavailable: "Unavailable · current reading unknown",
  "not-configured": "Not configured",
  pending: "Waiting for a reading",
};

function reading(region: ProviderRegion, serverTime: string | undefined, failed: boolean, elapsedSeconds: number): Reading {
  if (failed) return { state: "unavailable", checkedAt: null };
  const availability = region.availability;
  if (!availability) return { state: "pending", checkedAt: null };
  if (availability.state !== "available") return { state: availability.state, checkedAt: null };
  const now = serverTime ? Date.parse(serverTime) : NaN;
  const checked = Date.parse(availability.checked_at);
  const recordedAgeSeconds = (now - checked) / 1_000;
  if (!Number.isFinite(recordedAgeSeconds) || recordedAgeSeconds < 0 || !Number.isFinite(elapsedSeconds) || elapsedSeconds < 0 || !Number.isFinite(availability.stale_after_seconds) || availability.stale_after_seconds <= 0) {
    return { state: "unavailable", checkedAt: null };
  }
  const ageSeconds = recordedAgeSeconds + elapsedSeconds;
  if (ageSeconds > availability.stale_after_seconds) return { state: "stale", checkedAt: availability.checked_at };
  return { state: "current", counts: availability.counts, checkedAt: availability.checked_at };
}

function timestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
}

export function CloudAvailability({ system, failed, paused = false, elapsedSeconds = 0 }: { system: SystemInfo | undefined; failed: boolean; paused?: boolean; elapsedSeconds?: number }) {
  const provider = system?.providers.find((item) => item.name === "meraki");
  if (!provider?.enabled || !provider.regions?.length) return null;
  const regions = provider.regions;
  const failedReading = failed || paused || Boolean(provider?.last_error);
  return (
    <section aria-labelledby="cloud-availability-title" className="mb-4 min-w-0 overflow-hidden rounded-xl border border-line bg-panel shadow-panel">
      <header className="border-b border-line px-4 py-3">
        <h2 id="cloud-availability-title" className="text-[13px] font-semibold tracking-wide text-ink">{provider.label} availability</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted">Organization-wide Meraki readings, including devices outside Labkeeper’s testbed definitions. Each cloud is counted separately.</p>
      </header>
        <div role="region" aria-label="Organization-wide cloud availability table" tabIndex={0} className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent">
          <table className="w-full min-w-[760px] text-left text-xs">
            <caption className="sr-only">Current organization-wide device status by cloud, with imported matches shown separately. Unavailable or stale observations have no current device counts.</caption>
            <thead className="border-b border-line bg-panel-2 text-faint">
              <tr>
                <th scope="col" className="px-4 py-2 font-medium">Cloud / reading</th>
                {COLUMNS.map(({ key, label }) => <th key={key} scope="col" className="px-2 py-2 text-right font-medium">{label}</th>)}
                <th scope="col" className="px-4 py-2 text-right font-medium">Imported matches</th>
              </tr>
            </thead>
            <tbody>
              {regions.map((region) => {
                const value = reading(region, system?.server_time, failedReading, elapsedSeconds);
                return (
                  <tr key={region.host} className="border-b border-line last:border-0">
                    <th scope="row" className="px-4 py-3 font-normal">
                      <p className="font-semibold text-ink">{region.name}</p>
                      <p className={clsx("mt-0.5", value.state === "current" ? "text-muted" : "text-warn")}>{LABELS[value.state]}</p>
                      {value.checkedAt ? <p className="mt-0.5 text-faint"><time dateTime={value.checkedAt}>{timestamp(value.checkedAt)}</time></p> : null}
                    </th>
                    {COLUMNS.map(({ key }) => <td key={key} className={clsx("px-2 py-3 text-right font-mono tabular-nums", value.state === "current" && key === "offline" && value.counts.offline > 0 ? "font-semibold text-bad" : value.state === "current" && key === "alerting" && value.counts.alerting > 0 ? "text-warn" : "text-muted")}>{value.state === "current" ? value.counts[key] : <span aria-label="Unknown current count">—</span>}</td>)}
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-muted">{value.state === "current" ? region.devices : <span aria-label="Unknown current match count">—</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      <p className="border-t border-line px-4 py-2 text-xs leading-relaxed text-faint">Source: each region’s Meraki organization availability API. Imported matches count devices already in Labkeeper; they are separate from organization totals. Dormant and offline remain separate states.</p>
    </section>
  );
}
