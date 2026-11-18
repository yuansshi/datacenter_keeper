import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Rows3, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { RackBoard } from "../components/RackBoard";
import { Button, Card, EmptyState, Field, HealthBar, Input, Modal, PageHeader, Skeleton, clsx, useToast } from "../components/ui";
import { api } from "../lib/api";
import { readPref, writePref } from "../lib/user";

function NewRack({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [height, setHeight] = useState(42);
  const [site, setSite] = useState("");
  const create = useMutation({
    mutationFn: () => api.createRack({ name: name.trim(), height, site: site.trim() || null }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["racks"] });
      toast({ tone: "ok", title: `Created ${r.name}` });
      onClose();
      navigate(`/racks/${r.id}`);
    },
    onError: (e) => toast({ tone: "bad", title: "Could not create rack", body: (e as Error).message }),
  });
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New rack"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!name.trim()} loading={create.isPending} onClick={() => create.mutate()}>
            Create
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Name" hint="Match your location naming so testbed files place devices automatically, e.g. B1_C03">
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="B1_C03" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Height (U)">
            <Input type="number" min={1} max={100} value={height} onChange={(e) => setHeight(Number(e.target.value))} />
          </Field>
          <Field label="Site">
            <Input value={site} onChange={(e) => setSite(e.target.value)} placeholder="Building 1" />
          </Field>
        </div>
      </div>
    </Modal>
  );
}

export default function Racks() {
  const params = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: racks, isLoading } = useQuery({ queryKey: ["racks"], queryFn: api.racks, refetchInterval: 30_000 });
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<number[]>(() => readPref<number[]>("racks.selected", []));

  useEffect(() => {
    const id = Number(params.id);
    if (id && !selected.includes(id)) setSelected([id]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.id]);
  useEffect(() => {
    if (racks && racks.length && (!selected.length || !selected.every((id) => racks.some((r) => r.id === id)))) {
      const first = racks.find((r) => r.device_count > 0) ?? racks[0];
      setSelected([first.id]);
    }
  }, [racks, selected]);
  useEffect(() => writePref("racks.selected", selected), [selected]);

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteRack(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["racks"] });
      toast({ tone: "ok", title: "Rack deleted" });
      navigate("/racks");
    },
    onError: (e) => toast({ tone: "bad", title: "Could not delete", body: (e as Error).message }),
  });

  const list = useMemo(() => (racks ?? []).filter((r) => !q || r.name.toLowerCase().includes(q.toLowerCase()) || (r.site ?? "").toLowerCase().includes(q.toLowerCase())), [racks, q]);

  const toggle = (id: number, additive: boolean) => {
    setSelected((cur) => {
      if (!additive) return [id];
      if (cur.includes(id)) return cur.length > 1 ? cur.filter((x) => x !== id) : cur;
      return [...cur, id].slice(-4);
    });
    if (!additive) navigate(`/racks/${id}`, { replace: true });
  };

  const single = selected.length === 1 ? racks?.find((r) => r.id === selected[0]) : undefined;

  return (
    <div>
      <PageHeader
        title="Racks"
        subtitle="Drag devices to their physical slot. Shift-click racks to compare them side by side."
        icon={<Rows3 className="size-5" />}
        actions={
          <>
            {single && single.device_count === 0 && (
              <Button variant="danger" icon={<Trash2 className="size-4" />} onClick={() => remove.mutate(single.id)} loading={remove.isPending}>
                Delete rack
              </Button>
            )}
            <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>
              New rack
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-4 lg:flex-row">
        <Card className="lg:w-[240px] lg:shrink-0" bodyClassName="p-2">
          <Input icon={<Search className="size-3.5" />} placeholder="Find rack" value={q} onChange={(e) => setQ(e.target.value)} className="mb-2" />
          {isLoading ? (
            <Skeleton className="h-64" />
          ) : list.length === 0 ? (
            <EmptyState title="No racks" action={<Button size="xs" onClick={() => setCreating(true)}>Create one</Button>}>
              Racks are created from device <code className="font-mono">location</code> values in testbed files, or by hand.
            </EmptyState>
          ) : (
            <div className="max-h-[640px] space-y-0.5 overflow-y-auto">
              {list.map((r) => (
                <button
                  key={r.id}
                  onClick={(e) => toggle(r.id, e.shiftKey || e.metaKey || e.ctrlKey)}
                  className={clsx(
                    "w-full rounded-lg px-2.5 py-2 text-left transition-colors",
                    selected.includes(r.id) ? "bg-accent-soft" : "hover:bg-panel-2",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={clsx("truncate font-mono text-[12.5px] font-medium", selected.includes(r.id) && "text-accent")}>{r.name}</span>
                    <span className="num text-[11px] text-faint">{r.device_count}</span>
                  </div>
                  <HealthBar health={r.health} className="mt-1.5 h-1" />
                  <p className="mt-1 text-[10.5px] text-faint">
                    {r.used_units}/{r.height}U{r.floor_row ? ` · floor ${r.floor_row}${r.floor_col}` : ""}
                    {r.in_use ? ` · ${r.in_use} in use` : ""}
                  </p>
                </button>
              ))}
            </div>
          )}
          {racks && racks.some((r) => r.floor_plan_id) && (
            <Link to="/floor" className="mt-2 block rounded-lg px-2.5 py-2 text-[12px] text-accent hover:bg-panel-2">
              See racks on the floor plan →
            </Link>
          )}
        </Card>
        <div className="min-w-0 flex-1">{selected.length > 0 && <RackBoard rackIds={selected} />}</div>
      </div>
      <NewRack open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}
