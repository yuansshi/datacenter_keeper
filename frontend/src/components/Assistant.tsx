import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowUp, Check, ImagePlus, Loader2, Paperclip, RotateCcw, Sparkles, Square, Wrench, X } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, streamChat } from "../lib/api";
import { IconButton, Spinner, clsx } from "./ui";

const Markdown = lazy(() => import("./Markdown"));

type ToolCard = { id: string; name: string; title: string; input: unknown; done: boolean; ok?: boolean; summary?: string; error?: string | null; mutating?: boolean };
type Message =
  | { role: "user"; text: string; attachments: { name: string; url: string }[] }
  | { role: "assistant"; text: string; tools: ToolCard[]; status?: string; error?: string; streaming: boolean };

type Attachment = { name: string; media_type: string; data: string; url: string };

const SUGGESTIONS = [
  "Which testbeds are free right now?",
  "Book an available testbed for 2 hours",
  "What's offline, and since when?",
  "Test the consoles of every device in my booked testbed",
];

const MAX_IMAGE = 5 * 1024 * 1024;

function readFile(file: File): Promise<Attachment> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result);
      resolve({ name: file.name, media_type: file.type, data: url.split(",")[1] ?? "", url });
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function Assistant({ onClose }: { onClose: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const conversation = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const location = useLocation();
  const qc = useQueryClient();
  const { data: system } = useQuery({ queryKey: ["system"], queryFn: api.system });
  const available = system?.assistant.available ?? true;

  // Names the assistant may mention, so `code` spans can become links.
  const { data: names } = useQuery({
    queryKey: ["assistant-names"],
    queryFn: async () => {
      const [devices, testbeds] = await Promise.all([api.devices(), api.testbeds()]);
      const map = new Map<string, string>();
      devices.forEach((d) => map.set(d.name.toLowerCase(), `/devices/${d.id}`));
      testbeds.forEach((t) => map.set(t.name.toLowerCase(), `/testbeds/${t.id}`));
      return map;
    },
    staleTime: 60_000,
  });

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const addFiles = useCallback(async (files: FileList | File[]) => {
    setAttachError(null);
    const list = Array.from(files).slice(0, 4);
    for (const f of list) {
      const ok = ["image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"].includes(f.type);
      if (!ok) {
        setAttachError(`${f.name}: attach PNG, JPEG, WebP, GIF or PDF`);
        continue;
      }
      if (f.type !== "application/pdf" && f.size > MAX_IMAGE) {
        setAttachError(`${f.name} is over 5 MB; crop it to the floor map`);
        continue;
      }
      const a = await readFile(f);
      setAttachments((all) => [...all, a].slice(0, 4));
    }
  }, []);

  const send = useCallback(
    async (text: string) => {
      const message = text.trim();
      if ((!message && attachments.length === 0) || busy) return;
      setInput("");
      const sent = attachments;
      setAttachments([]);
      setBusy(true);
      setMessages((m) => [
        ...m,
        { role: "user", text: message || "(see attachment)", attachments: sent.map((a) => ({ name: a.name, url: a.url })) },
        { role: "assistant", text: "", tools: [], streaming: true, status: "thinking" },
      ]);
      const update = (fn: (msg: Extract<Message, { role: "assistant" }>) => void) =>
        setMessages((all) => {
          const copy = [...all];
          const last = copy[copy.length - 1];
          if (last?.role === "assistant") {
            const next = { ...last, tools: [...last.tools] };
            fn(next);
            copy[copy.length - 1] = next;
          }
          return copy;
        });
      const ctrl = new AbortController();
      abort.current = ctrl;
      let mutated = false;
      try {
        const now = new Date();
        await streamChat(
          {
            conversation_id: conversation.current,
            message: message || "Here is a floor plan image. Read it and set it up.",
            page: location.pathname + location.search,
            local_time: now.toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" }),
            tz_offset_minutes: -now.getTimezoneOffset(),
            attachments: sent.map(({ name, media_type, data }) => ({ name, media_type, data })),
          },
          (ev) => {
            switch (ev.type) {
              case "conversation":
                conversation.current = String(ev.id);
                break;
              case "status":
                update((m) => (m.status = String(ev.text)));
                break;
              case "text":
                update((m) => {
                  m.text += String(ev.delta);
                  m.status = undefined;
                });
                break;
              case "tool_start":
                update((m) => {
                  m.status = undefined;
                  m.tools.push({ id: String(ev.id), name: String(ev.name), title: String(ev.title), input: ev.input, done: false });
                });
                break;
              case "tool_end":
                if (ev.mutating && ev.ok) mutated = true;
                update((m) => {
                  const t = m.tools.find((x) => x.id === ev.id);
                  if (t) Object.assign(t, { done: true, ok: Boolean(ev.ok), summary: String(ev.summary ?? ""), error: (ev.error as string) ?? null, mutating: Boolean(ev.mutating) });
                  m.status = "thinking";
                });
                break;
              case "error":
                update((m) => (m.error = String(ev.message)));
                break;
              case "done":
                update((m) => {
                  m.streaming = false;
                  m.status = undefined;
                });
                break;
            }
          },
          ctrl.signal,
        );
      } catch (err) {
        if ((err as Error).name !== "AbortError") update((m) => (m.error = (err as Error).message));
      } finally {
        update((m) => {
          m.streaming = false;
          m.status = undefined;
        });
        setBusy(false);
        abort.current = null;
        if (mutated) qc.invalidateQueries();
      }
    },
    [attachments, busy, location.pathname, location.search, qc],
  );

  useEffect(() => {
    const onAsk = (e: Event) => send(String((e as CustomEvent).detail ?? ""));
    window.addEventListener("lk:ask", onAsk);
    return () => window.removeEventListener("lk:ask", onAsk);
  }, [send]);

  const reset = () => {
    abort.current?.abort();
    if (conversation.current) fetch(`/api/v1/assistant/conversations/${conversation.current}`, { method: "DELETE" }).catch(() => {});
    conversation.current = null;
    setMessages([]);
  };

  const linkify = useMemo(() => names ?? new Map<string, string>(), [names]);

  return (
    <aside
      className="relative flex w-full max-w-[100vw] shrink-0 flex-col border-l border-line bg-panel sm:w-[420px]"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
      }}
    >
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-3">
        <div className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-lg bg-accent-soft text-accent">
            <Sparkles className="size-4" />
          </span>
          <div>
            <p className="text-[13px] font-semibold leading-tight">Labkeeper assistant</p>
            <p className="text-[11px] leading-tight text-faint">{system?.assistant.model ?? "Claude"} · can book, release and test consoles</p>
          </div>
        </div>
        <div className="flex items-center">
          <IconButton label="New conversation" onClick={reset}>
            <RotateCcw className="size-4" />
          </IconButton>
          <IconButton label="Close assistant" onClick={onClose}>
            <X className="size-4" />
          </IconButton>
        </div>
      </div>

      <div ref={scroller} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-4">
        {!available && (
          <div className="rounded-xl border border-warn/40 bg-warn/10 p-3 text-[12.5px] text-ink">
            <p className="flex items-center gap-1.5 font-medium text-warn">
              <AlertTriangle className="size-4" /> Assistant not configured
            </p>
            <p className="mt-1 text-muted">{system?.assistant.reason}</p>
          </div>
        )}
        {messages.length === 0 && (
          <div className="px-1 pt-2">
            <p className="text-[15px] font-semibold">How can I help in the lab?</p>
            <p className="mt-1 text-[12.5px] text-muted">
              I can find devices, check live status, book or release testbeds, test console lines, place devices in racks, and turn a floor-map
              image into a 3D floor plan. Drop an image here to start one.
            </p>
            <div className="mt-4 space-y-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  disabled={!available}
                  onClick={() => send(s)}
                  className="block w-full rounded-lg border border-line bg-panel-2 px-3 py-2 text-left text-[12.5px] text-ink transition-colors hover:border-accent/50 disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="flex flex-col items-end gap-1.5">
              {m.attachments.map((a) => (
                <img key={a.url.slice(-32)} src={a.url} alt={a.name} className="max-h-36 rounded-lg border border-line object-contain" />
              ))}
              <div className="max-w-[88%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent px-3 py-2 text-[13px] text-accent-ink">{m.text}</div>
            </div>
          ) : (
            <div key={i} className="space-y-2">
              {m.tools.map((t) => (
                <div key={t.id} className="flex items-start gap-2 rounded-lg border border-line bg-panel-2 px-2.5 py-1.5 text-[12px]">
                  <span className="mt-0.5">
                    {!t.done ? (
                      <Loader2 className="size-3.5 animate-spin text-accent" />
                    ) : t.ok ? (
                      <Check className={clsx("size-3.5", t.mutating ? "text-warn" : "text-ok")} />
                    ) : (
                      <AlertTriangle className="size-3.5 text-bad" />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-1 font-medium capitalize text-ink">
                      <Wrench className="size-3 text-faint" />
                      {t.title}
                    </p>
                    {t.summary && <p className="text-muted">{t.summary}</p>}
                    {t.error && <p className="text-bad">{t.error}</p>}
                    {!t.summary && !t.error && t.input != null && Object.keys(t.input as object).length > 0 && (
                      <p className="truncate font-mono text-[10.5px] text-faint">{JSON.stringify(t.input)}</p>
                    )}
                  </div>
                </div>
              ))}
              {m.text && (
                <div className="md text-[13px] leading-relaxed">
                  <Suspense fallback={<p className="whitespace-pre-wrap">{m.text}</p>}>
                    <Markdown text={m.text} links={linkify} />
                  </Suspense>
                </div>
              )}
              {m.status && (
                <p className="flex items-center gap-1.5 text-[12px] text-faint">
                  <Spinner className="size-3" />
                  {m.status === "thinking" ? "Thinking…" : m.status}
                </p>
              )}
              {m.error && (
                <p className="rounded-lg border border-bad/30 bg-bad/10 px-2.5 py-1.5 text-[12px] text-bad">{m.error}</p>
              )}
            </div>
          ),
        )}
      </div>

      <div className="border-t border-line p-3">
        {attachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachments.map((a, i) => (
              <div key={i} className="relative">
                {a.media_type.startsWith("image/") ? (
                  <img src={a.url} alt={a.name} className="size-14 rounded-lg border border-line object-cover" />
                ) : (
                  <div className="flex size-14 items-center justify-center rounded-lg border border-line bg-panel-2 text-[10px] text-muted">PDF</div>
                )}
                <button
                  className="absolute -right-1.5 -top-1.5 rounded-full bg-panel-3 p-0.5 text-muted hover:text-ink"
                  onClick={() => setAttachments((all) => all.filter((_, j) => j !== i))}
                  aria-label={`Remove ${a.name}`}
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        {attachError && <p className="mb-2 text-[11.5px] text-bad">{attachError}</p>}
        <form
          className="flex items-end gap-2 rounded-xl border border-line bg-panel-2 p-1.5 focus-within:border-accent/50"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,application/pdf"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <IconButton label="Attach floor map image" type="button" onClick={() => fileInput.current?.click()} className="size-8">
            <Paperclip className="size-4" />
          </IconButton>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={(e) => {
              const files = Array.from(e.clipboardData.files);
              if (files.length) {
                e.preventDefault();
                addFiles(files);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            rows={1}
            disabled={!available}
            placeholder={available ? "Ask about devices, testbeds, bookings…" : "Assistant unavailable"}
            className="max-h-40 min-h-8 flex-1 resize-none bg-transparent py-1.5 text-[13px] outline-none placeholder:text-faint"
          />
          {busy ? (
            <IconButton label="Stop" type="button" onClick={() => abort.current?.abort()} className="size-8 bg-panel-3">
              <Square className="size-3.5" />
            </IconButton>
          ) : (
            <button
              type="submit"
              disabled={!available || (!input.trim() && attachments.length === 0)}
              className="inline-flex size-8 items-center justify-center rounded-lg bg-accent text-accent-ink disabled:opacity-40"
              aria-label="Send"
            >
              <ArrowUp className="size-4" />
            </button>
          )}
        </form>
        <p className="mt-1.5 flex items-center gap-1 text-[10.5px] text-faint">
          <ImagePlus className="size-3" /> Drop or paste a floor-map image to build a floor plan ·{" "}
          <Link to="/settings" className="underline-offset-2 hover:underline">
            use from Claude Code
          </Link>
        </p>
      </div>

      {dragging && (
        <div className="pointer-events-none absolute inset-2 flex items-center justify-center rounded-2xl border-2 border-dashed border-accent bg-accent-soft text-[13px] font-medium text-accent">
          Drop a floor map image or PDF
        </div>
      )}
    </aside>
  );
}
