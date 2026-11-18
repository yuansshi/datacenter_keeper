import { useQuery } from "@tanstack/react-query";
import { CalendarRange, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { BookingActions, BookingDialog, BookingLine } from "../components/booking";
import { Timeline } from "../components/Timeline";
import { Card, EmptyState, Input, PageHeader, Segmented } from "../components/ui";
import { api } from "../lib/api";
import type { TestbedSummary } from "../lib/types";
import { useUser } from "../lib/user";

export default function Bookings() {
  const user = useUser();
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"all" | "booked" | "mine">("all");
  const [dialog, setDialog] = useState<{ testbed: TestbedSummary; at: Date } | null>(null);
  const { data: testbeds } = useQuery({ queryKey: ["testbeds"], queryFn: () => api.testbeds(), refetchInterval: 20_000 });
  const { data: upcoming } = useQuery({
    queryKey: ["bookings", "active"],
    queryFn: () => api.bookings({ active_only: true }),
    refetchInterval: 20_000,
  });

  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    const bookedIds = new Set((upcoming ?? []).map((b) => b.testbed_id));
    const mineIds = new Set((upcoming ?? []).filter((b) => b.owner === user).map((b) => b.testbed_id));
    return (testbeds ?? []).filter(
      (t) =>
        (!term || t.name.toLowerCase().includes(term) || (t.group ?? "").toLowerCase().includes(term)) &&
        (only === "all" || (only === "booked" ? bookedIds.has(t.id) || t.status === "blocked" : mineIds.has(t.id))),
    );
  }, [testbeds, q, only, upcoming, user]);

  const mine = (upcoming ?? []).filter((b) => b.owner === user);

  return (
    <div className="space-y-4">
      <PageHeader title="Bookings" subtitle="Who has which testbed, and when it frees up" icon={<CalendarRange className="size-5" />} />
      <div className="flex flex-wrap items-center gap-2">
        <Input icon={<Search className="size-4" />} placeholder="Filter testbeds" value={q} onChange={(e) => setQ(e.target.value)} className="w-64" />
        <Segmented
          value={only}
          onChange={setOnly}
          options={[
            { value: "all", label: "All testbeds" },
            { value: "booked", label: "Booked or blocked" },
            { value: "mine", label: "Mine" },
          ]}
        />
      </div>
      <Card bodyClassName="p-0">
        {rows.length === 0 ? (
          <EmptyState title="No testbeds match" />
        ) : (
          <Timeline
            testbeds={rows.map((t) => ({ id: t.id, name: t.name, status: t.status }))}
            onCreate={(id, at) => {
              const t = testbeds?.find((x) => x.id === id);
              if (t && t.status !== "maintenance") setDialog({ testbed: t, at });
            }}
          />
        )}
      </Card>
      <Card title="Your bookings" bodyClassName="divide-y divide-line">
        {mine.length === 0 ? (
          <EmptyState title="You have no active or upcoming bookings" />
        ) : (
          mine.map((b) => (
            <div key={b.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <BookingLine booking={b} />
              <BookingActions booking={b} />
            </div>
          ))
        )}
      </Card>
      {dialog && <BookingDialog testbed={dialog.testbed} open onClose={() => setDialog(null)} initialStart={dialog.at} />}
    </div>
  );
}
