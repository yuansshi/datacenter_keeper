import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileSpreadsheet, Grid3x3, ImageUp, Map as MapIcon, Sparkles, Upload } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { FloorPlanEditor } from "../components/FloorPlanEditor";
import { FloorSourcesCard } from "../components/FloorSources";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Skeleton, Spinner, clsx, useToast } from "../components/ui";
import { api } from "../lib/api";
import { blankPlan, suggestPrefix } from "../lib/floor";
import { relative } from "../lib/format";
import type { FloorPlanInput } from "../lib/types";

type Draft = { plan: FloorPlanInput; imageToken: string | null; imageUrl: string | null; method: "vision" | "grid" | "blank" };

function DropZone({ onFile, busy, assistantReady }: { onFile: (f: File) => void; busy: boolean; assistantReady: boolean }) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files[0];
        if (f) onFile(f);
      }}
      onClick={() => !busy && input.current?.click()}
      className={clsx(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
        over ? "border-accent bg-accent-soft" : "border-line-strong hover:border-accent/60 hover:bg-panel-2",
      )}
    >
      <input
        ref={input}
        type="file"
        hidden
        accept="image/png,image/jpeg,image/webp,image/gif,application/pdf,.csv,.tsv,.yaml,.yml,.json,.txt"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
      {busy ? (
        <>
          <Spinner className="size-6 text-accent" />
          <p className="text-[14px] font-medium">Reading the floor plan…</p>
          <p className="text-[12px] text-muted">Claude is mapping rows, columns, racks, benches and pillars. This takes a few seconds to a minute.</p>
        </>
      ) : (
        <>
          <div className="flex size-11 items-center justify-center rounded-xl bg-accent-soft text-accent">
            <ImageUp className="size-5" />
          </div>
          <p className="text-[14px] font-medium">Drop a floor map here</p>
          <p className="max-w-md text-[12.5px] text-muted">
            A screenshot or PDF of your lab layout (for example a DCIM floor map) is read with AI into a grid you can review.
            CSV, YAML or JSON grids, and exports from connected sources, are imported directly.
          </p>
          <div className="mt-1 flex flex-wrap justify-center gap-1.5">
            <Badge tone={assistantReady ? "accent" : "warn"}>
              <Sparkles className="size-3" /> {assistantReady ? "PNG · JPEG · WebP · PDF" : "image reading needs ANTHROPIC_API_KEY"}
            </Badge>
            <Badge>
              <FileSpreadsheet className="size-3" /> CSV · YAML · JSON
            </Badge>
          </div>
        </>
      )}
    </div>
  );
}

export default function FloorPlans() {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const { data: plans, isLoading } = useQuery({ queryKey: ["floorplans"], queryFn: api.floorPlans });
  const { data: racks } = useQuery({ queryKey: ["racks"], queryFn: api.racks });
  const { data: system } = useQuery({ queryKey: ["system"], queryFn: api.system });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [hint, setHint] = useState("");
  const rackNames = (racks ?? []).map((r) => r.name);

  const extract = useMutation({
    mutationFn: (file: File) => api.extractFloorPlan(file, hint || undefined),
    onSuccess: (res, file) => {
      if (res.method === "source") {
        qc.invalidateQueries({ queryKey: ["floorplans"] });
        const first = res.plans[0];
        toast({
          tone: "ok",
          title: first?.created ? `Imported ${first.name} from ${res.label}` : `Updated ${first?.name} from ${res.label}`,
          body: first?.created ? "Layout and metrics come from the source from now on." : `${first?.changes ?? 0} cell changes`,
        });
        if (first) navigate(`/floor/${first.id}`);
        return;
      }
      const plan = { ...res.draft };
      const prefix = suggestPrefix(plan, rackNames);
      if (prefix !== null && !plan.rack_prefix) plan.rack_prefix = prefix;
      setDraft({
        plan,
        imageToken: res.image_token,
        imageUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : null,
        method: res.method,
      });
      toast({
        tone: "ok",
        title: res.method === "vision" ? "Floor plan read" : "Grid imported",
        body: `${plan.rows.length} rows × ${plan.columns.length} columns · ${plan.cells.filter((c) => c.type === "rack").length} racks`,
      });
    },
    onError: (e) => toast({ tone: "bad", title: "Could not read the floor plan", body: (e as Error).message }),
  });

  const save = useMutation({
    mutationFn: (d: Draft) => api.createFloorPlan({ ...d.plan, image_token: d.imageToken }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["floorplans"] });
      qc.invalidateQueries({ queryKey: ["racks"] });
      toast({ tone: "ok", title: `Saved ${p.name}`, body: `${p.racks.filter((r) => r.rack).length} racks linked` });
      navigate(`/floor/${p.id}`);
    },
    onError: (e) => toast({ tone: "bad", title: "Could not save", body: (e as Error).message }),
  });

  const onFile = useCallback((f: File) => extract.mutate(f), [extract]);

  if (draft) {
    const racksInPlan = draft.plan.cells.filter((c) => c.type === "rack" || c.type === "empty_rack");
    const willLink = racksInPlan.filter((c) => rackNames.some((n) => n.toUpperCase() === `${draft.plan.rack_prefix}${c.label ?? `${c.row}${c.col}`}`.toUpperCase())).length;
    return (
      <div>
        <PageHeader
          title="Review floor plan"
          subtitle={
            draft.method === "vision"
              ? "Read from your image with AI. Check rows, columns and cells, fix anything off, then save."
              : "Adjust the grid, then save."
          }
          icon={<MapIcon className="size-5" />}
          actions={
            <>
              <Button variant="ghost" onClick={() => setDraft(null)}>
                Discard
              </Button>
              <Button variant="primary" loading={save.isPending} disabled={!draft.plan.name.trim()} onClick={() => save.mutate(draft)}>
                Save floor plan
              </Button>
            </>
          }
        />
        <div className="mb-3 flex flex-wrap gap-2 text-[12px]">
          <Badge>{draft.plan.rows.length} rows</Badge>
          <Badge>{draft.plan.columns.length} columns</Badge>
          <Badge tone="accent">{racksInPlan.length} rack positions</Badge>
          <Badge tone={willLink ? "ok" : "warn"}>{willLink} match existing racks</Badge>
        </div>
        {draft.plan.notes && (
          <p className="mb-3 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[12.5px] text-ink">
            <span className="font-medium text-warn">Double-check: </span>
            {draft.plan.notes}
          </p>
        )}
        <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
          {draft.imageUrl && (
            <Card title="Source image" bodyClassName="p-2">
              <img src={draft.imageUrl} alt="Uploaded floor map" className="max-h-[640px] w-full rounded-lg object-contain" />
            </Card>
          )}
          <Card title="Grid" className={draft.imageUrl ? "" : "2xl:col-span-2"}>
            <FloorPlanEditor plan={draft.plan} onChange={(plan) => setDraft({ ...draft, plan })} rackNames={rackNames} />
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Floor plans"
        subtitle="Rooms as interactive 3D maps. Click a rack to fill it with devices."
        icon={<MapIcon className="size-5" />}
      />
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
        <Card title="New floor plan">
          <DropZone onFile={onFile} busy={extract.isPending} assistantReady={system?.assistant.available ?? false} />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Input
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder="Optional hint for the AI, e.g. “rows Z to R, columns 01-20, numbers are kW”"
              className="min-w-0 flex-1"
            />
            <Button icon={<Grid3x3 className="size-4" />} onClick={() => setDraft({ plan: blankPlan(), imageToken: null, imageUrl: null, method: "blank" })}>
              Start from a blank grid
            </Button>
          </div>
          <p className="mt-2 flex items-center gap-1.5 text-[11.5px] text-faint">
            <Upload className="size-3" /> You can also drop the image into the assistant (⌘J) and ask it to set the room up.
          </p>
        </Card>
          <FloorSourcesCard />
        </div>
        <Card title="Saved plans" className="self-start" bodyClassName="divide-y divide-line">
          {isLoading ? (
            <Skeleton className="m-4 h-24" />
          ) : !plans?.length ? (
            <EmptyState icon={<MapIcon className="size-5" />} title="No floor plans yet">
              Drop your lab’s floor map to create the first one.
            </EmptyState>
          ) : (
            plans.map((p) => (
              <Link key={p.id} to={`/floor/${p.id}`} className="block px-4 py-3 hover:bg-panel-2">
                <p className="flex items-center gap-2 text-[13.5px] font-semibold">
                  {p.name}
                  {p.source_ref && <Badge tone="accent">{p.source_ref.split(":")[0].toUpperCase()}</Badge>}
                </p>
                <p className="text-[11.5px] text-faint">
                  {p.site ? `${p.site} · ` : ""}
                  {p.rows}×{p.columns} grid · {p.rack_cells} rack positions · {p.linked_racks} linked · {p.synced_at ? `synced ${relative(p.synced_at)}` : `updated ${relative(p.updated_at)}`}
                </p>
              </Link>
            ))
          )}
        </Card>
      </div>
    </div>
  );
}
