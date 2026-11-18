import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Eye, FileCode2, FileUp, FolderOpen, RefreshCw } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Segmented, Skeleton, clsx, useToast } from "../components/ui";
import { api } from "../lib/api";
import { relative } from "../lib/format";

export default function ImportPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading } = useQuery({ queryKey: ["imports"], queryFn: api.imports, refetchInterval: 15_000 });
  const [filter, setFilter] = useState<"all" | "testbed" | "base" | "invalid">("all");
  const [q, setQ] = useState("");
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const rescan = useMutation({
    mutationFn: api.rescan,
    onSuccess: (s) => {
      qc.invalidateQueries();
      toast({ tone: s.invalid ? "bad" : "ok", title: "Re-imported testbed files", body: `${s.testbeds} testbeds · ${s.devices} devices · ${s.invalid} with errors` });
    },
  });
  const upload = useMutation({
    mutationFn: (files: File[]) => api.uploadTestbeds(files),
    onSuccess: (r) => {
      qc.invalidateQueries();
      toast({ tone: "ok", title: `Uploaded ${r.saved.length} file${r.saved.length === 1 ? "" : "s"}`, body: `${r.summary.testbeds} testbeds now` });
    },
    onError: (e) => toast({ tone: "bad", title: "Upload failed", body: (e as Error).message }),
  });

  const sources = useMemo(
    () =>
      (data?.sources ?? []).filter(
        (s) => (filter === "all" || s.kind === filter) && (!q || s.relative.toLowerCase().includes(q.toLowerCase())),
      ),
    [data, filter, q],
  );
  const counts = useMemo(() => {
    const c = { all: 0, testbed: 0, base: 0, invalid: 0 };
    data?.sources.forEach((s) => {
      c.all++;
      c[s.kind]++;
    });
    return c;
  }, [data]);
  const s = data?.last_summary;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Testbed files"
        subtitle="pyATS testbed YAML is the source of truth. Labkeeper watches these folders and re-imports on every save."
        icon={<FileCode2 className="size-5" />}
        actions={
          <Button icon={<RefreshCw className="size-4" />} loading={rescan.isPending} onClick={() => rescan.mutate()}>
            Rescan now
          </Button>
        }
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Watched folders" className="lg:col-span-2">
          {isLoading ? (
            <Skeleton className="h-20" />
          ) : (
            <ul className="space-y-2">
              {data?.roots.map((r) => (
                <li key={r} className="flex items-center gap-2 font-mono text-[12px]">
                  <FolderOpen className="size-4 shrink-0 text-accent" />
                  <span className="break-all">{r}</span>
                  {r === data.uploads_dir && <Badge>uploads</Badge>}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-[12px] text-muted">
            Add folders in <span className="font-mono">labkeeper.yaml</span> (<span className="font-mono">testbeds.paths</span>) or start with{" "}
            <span className="font-mono">labkeeper serve -t ~/path/to/testbeds</span>. Files nobody else extends become testbeds; files that are
            extended are shared building blocks.
          </p>
          {data?.watching && <p className="mt-1 text-[11.5px] text-ok">● watching for changes</p>}
        </Card>
        <Card title="Last import">
          {s ? (
            <dl className="grid grid-cols-2 gap-y-1.5 text-[12.5px]">
              <dt className="text-muted">Files</dt>
              <dd className="num text-right">{s.files}</dd>
              <dt className="text-muted">Testbeds</dt>
              <dd className="num text-right">{s.testbeds}</dd>
              <dt className="text-muted">Base files</dt>
              <dd className="num text-right">{s.bases}</dd>
              <dt className="text-muted">Devices</dt>
              <dd className="num text-right">{s.devices}</dd>
              <dt className="text-muted">With errors</dt>
              <dd className={clsx("num text-right", s.invalid && "text-bad")}>{s.invalid}</dd>
              <dt className="text-muted">Finished</dt>
              <dd className="text-right">{relative(s.finished_at)}</dd>
            </dl>
          ) : (
            <p className="text-[13px] text-muted">No import yet.</p>
          )}
        </Card>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const files = Array.from(e.dataTransfer.files).filter((f) => /\.ya?ml$/i.test(f.name));
          if (files.length) upload.mutate(files);
        }}
        onClick={() => input.current?.click()}
        className={clsx(
          "flex cursor-pointer items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-6 text-[13px] transition-colors",
          over ? "border-accent bg-accent-soft" : "border-line-strong text-muted hover:border-accent/60",
        )}
      >
        <input
          ref={input}
          type="file"
          accept=".yaml,.yml"
          multiple
          hidden
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) upload.mutate(files);
            e.target.value = "";
          }}
        />
        <FileUp className="size-5 text-accent" />
        {upload.isPending ? "Uploading…" : "Drop pyATS testbed YAML files here to add them (saved to the uploads folder)"}
      </div>

      <Card bodyClassName="">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
          <Segmented
            value={filter}
            onChange={setFilter}
            options={[
              { value: "all", label: "All", count: counts.all },
              { value: "testbed", label: "Testbeds", count: counts.testbed },
              { value: "base", label: "Base files", count: counts.base },
              { value: "invalid", label: "Errors", count: counts.invalid },
            ]}
          />
          <Input placeholder="Filter by path" value={q} onChange={(e) => setQ(e.target.value)} className="w-60" />
        </div>
        {sources.length === 0 ? (
          <EmptyState title="No files" />
        ) : (
          <div className="max-h-[560px] overflow-y-auto">
            <table className="w-full text-left text-[12.5px]">
              <tbody>
                {sources.map((src) => (
                  <tr key={src.path} className="border-b border-line last:border-0">
                    <td className="w-8 py-2 pl-4">
                      {src.kind === "invalid" ? <AlertTriangle className="size-4 text-bad" /> : <CheckCircle2 className={clsx("size-4", src.kind === "testbed" ? "text-ok" : "text-faint")} />}
                    </td>
                    <td className="px-2 py-2">
                      <p className="font-mono">{src.relative}</p>
                      {src.error && <p className="text-[11.5px] text-bad">{src.error}</p>}
                    </td>
                    <td className="px-2 py-2">
                      <Badge tone={src.kind === "testbed" ? "accent" : src.kind === "invalid" ? "bad" : "neutral"}>{src.kind}</Badge>
                    </td>
                    <td className="num px-2 py-2 text-muted">{src.device_count} devices</td>
                    <td className="px-2 py-2 pr-4 text-right">
                      {src.testbed_id && (
                        <Link to={`/testbeds/${src.testbed_id}`} className="inline-flex items-center gap-1 text-accent hover:underline">
                          <Eye className="size-3.5" /> Open
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
