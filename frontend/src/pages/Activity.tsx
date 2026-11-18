import { useInfiniteQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Avatar, Button, Card, EmptyState, Input, PageHeader, Segmented, clsx } from "../components/ui";
import { api } from "../lib/api";
import { stamp } from "../lib/format";
import type { EventItem } from "../lib/types";

const ACTION_TONE: Record<string, string> = {
  booked: "var(--accent)",
  released: "var(--info)",
  cancelled: "var(--idle)",
  extended: "var(--accent)",
  status: "var(--warn)",
  console_test: "var(--violet)",
  imported: "var(--info)",
  racked: "var(--ok)",
  unracked: "var(--idle)",
  created: "var(--ok)",
  updated: "var(--info)",
  deleted: "var(--bad)",
  maintenance: "var(--warn)",
  saved: "var(--ok)",
};

const ACTION_LABEL: Record<string, string> = { console_test: "Console test", status: "Status" };

function objectLink(e: EventItem): string | null {
  if (e.object_id == null) return null;
  switch (e.object_type) {
    case "device":
      return `/devices/${e.object_id}`;
    case "testbed":
      return `/testbeds/${e.object_id}`;
    case "rack":
      return `/racks/${e.object_id}`;
    case "floor_plan":
      return `/floor/${e.object_id}`;
    default:
      return null;
  }
}

export function ActivityTable({ events, compact }: { events: EventItem[]; compact?: boolean }) {
  if (!events.length) return <EmptyState icon={<History className="size-5" />} title="No activity yet" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-[12.5px]">
        <thead>
          <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
            <th className="px-4 py-2 font-semibold">Time</th>
            <th className="px-2 py-2 font-semibold">User</th>
            <th className="px-2 py-2 font-semibold">Action</th>
            <th className="px-2 py-2 font-semibold">Object</th>
            {!compact && <th className="px-2 py-2 font-semibold">Type</th>}
            <th className="px-2 py-2 pr-4 font-semibold">Details</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => {
            const to = objectLink(e);
            const color = ACTION_TONE[e.action] ?? "var(--muted)";
            return (
              <tr key={e.id} className="border-b border-line last:border-0 hover:bg-panel-2/60">
                <td className="num whitespace-nowrap px-4 py-2 text-muted">{stamp(e.ts)}</td>
                <td className="whitespace-nowrap px-2 py-2">
                  <span className="flex items-center gap-1.5">
                    <Avatar name={e.actor} size={18} />
                    <span className="max-w-[140px] truncate">{e.actor}</span>
                  </span>
                </td>
                <td className="px-2 py-2">
                  <span
                    className="rounded-md px-1.5 py-0.5 text-[11px] font-semibold capitalize"
                    style={{ color, background: `color-mix(in srgb, ${color} 13%, transparent)` }}
                  >
                    {ACTION_LABEL[e.action] ?? e.action}
                  </span>
                </td>
                <td className="max-w-[180px] truncate px-2 py-2 font-mono">
                  {to ? (
                    <Link to={to} className="hover:text-accent">
                      {e.object_name}
                    </Link>
                  ) : (
                    e.object_name
                  )}
                </td>
                {!compact && <td className="px-2 py-2 capitalize text-muted">{e.object_type.replace("_", " ")}</td>}
                <td className={clsx("px-2 py-2 pr-4 text-muted", compact ? "max-w-[260px] truncate" : "max-w-[520px]")}>{e.message}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function Activity() {
  const [type, setType] = useState<string>("all");
  const [actor, setActor] = useState("");
  const q = useInfiniteQuery({
    queryKey: ["events", type, actor],
    queryFn: ({ pageParam }) =>
      api.events({ limit: 100, object_type: type === "all" ? undefined : type, actor: actor || undefined, before_id: pageParam ?? undefined }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => (last.length === 100 ? last[last.length - 1].id : null),
    refetchInterval: 20_000,
  });
  const events = q.data?.pages.flat() ?? [];
  return (
    <div>
      <PageHeader title="Activity" subtitle="Every booking, release, status change, console test, import and rack move" icon={<History className="size-5" />} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmented
          value={type}
          onChange={setType}
          options={[
            { value: "all", label: "All" },
            { value: "testbed", label: "Testbeds" },
            { value: "device", label: "Devices" },
            { value: "rack", label: "Racks" },
            { value: "floor_plan", label: "Floor plans" },
            { value: "import", label: "Imports" },
          ]}
        />
        <Input placeholder="Filter by user" value={actor} onChange={(e) => setActor(e.target.value)} className="w-48" />
      </div>
      <Card bodyClassName="">
        <ActivityTable events={events} />
        {q.hasNextPage && (
          <div className="border-t border-line p-3 text-center">
            <Button loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
              Load older
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
