import clsx from "clsx";
import { X } from "lucide-react";
import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { STATUS_COLOR, STATUS_LABEL, TESTBED_COLOR, TESTBED_LABEL } from "../lib/format";
import type { DeviceStatus, StatusCounts, TestbedStatus } from "../lib/types";

export { clsx };

// ---- buttons -----------------------------------------------------------------------------------

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "xs" | "sm" | "md";

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; icon?: ReactNode; loading?: boolean }
>(function Button({ variant = "secondary", size = "sm", icon, loading, className, children, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={clsx(
        "inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        size === "xs" && "h-7 px-2 text-xs",
        size === "sm" && "h-8 px-3 text-[13px]",
        size === "md" && "h-10 px-4 text-sm",
        variant === "primary" && "bg-accent text-accent-ink hover:bg-accent-strong",
        variant === "secondary" && "border border-line bg-panel-2 text-ink hover:border-line-strong hover:bg-panel-3",
        variant === "outline" && "border border-line-strong text-ink hover:bg-panel-2",
        variant === "ghost" && "text-muted hover:bg-panel-2 hover:text-ink",
        variant === "danger" && "border border-bad/40 bg-bad/10 text-bad hover:bg-bad/20",
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner className="size-3.5" /> : icon}
      {children}
    </button>
  );
});

export function IconButton({
  label,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      aria-label={label}
      title={label}
      className={clsx(
        "inline-flex size-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-panel-2 hover:text-ink",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={clsx("animate-spin", className ?? "size-4")} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

// ---- surfaces ----------------------------------------------------------------------------------

export function Card({
  title,
  actions,
  children,
  className,
  bodyClassName,
  subtitle,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={clsx("rounded-xl border border-line bg-panel shadow-panel", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            {title && <h2 className="truncate text-[13px] font-semibold tracking-wide text-ink">{title}</h2>}
            {subtitle && <p className="truncate text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={clsx(bodyClassName ?? "p-4")}>{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  icon,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="flex min-w-0 items-center gap-3">
        {icon && (
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-line bg-panel-2 text-accent">
            {icon}
          </div>
        )}
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <div className="mt-0.5 text-[13px] text-muted">{subtitle}</div>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && <div className="mb-1 flex size-11 items-center justify-center rounded-xl bg-panel-2 text-faint">{icon}</div>}
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="max-w-md text-[13px] text-muted">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("pulse rounded-md bg-panel-3", className)} />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line-strong bg-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-muted">
      {children}
    </kbd>
  );
}

// ---- status ------------------------------------------------------------------------------------

export function StatusDot({ status, className, pulse }: { status: DeviceStatus; className?: string; pulse?: boolean }) {
  return (
    <span
      className={clsx("inline-block size-2 shrink-0 rounded-full", pulse && status === "offline" && "pulse", className)}
      style={{ background: STATUS_COLOR[status] ?? STATUS_COLOR.unknown, boxShadow: `0 0 0 3px color-mix(in srgb, ${STATUS_COLOR[status]} 18%, transparent)` }}
      aria-label={STATUS_LABEL[status]}
    />
  );
}

export function StatusPill({ status }: { status: DeviceStatus }) {
  const color = STATUS_COLOR[status] ?? STATUS_COLOR.unknown;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ color, background: `color-mix(in srgb, ${color} 13%, transparent)` }}
    >
      <span className="size-1.5 rounded-full" style={{ background: color }} />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function TestbedPill({ status, className }: { status: TestbedStatus; className?: string }) {
  const color = TESTBED_COLOR[status];
  return (
    <span
      className={clsx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold", className)}
      style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      <span className="size-1.5 rounded-full" style={{ background: color }} />
      {TESTBED_LABEL[status]}
    </span>
  );
}

export function HealthBar({ health, className }: { health: StatusCounts; className?: string }) {
  const total = health.online + health.alerting + health.offline + health.dormant + health.unknown;
  if (!total) return <div className={clsx("h-1.5 rounded-full bg-panel-3", className)} />;
  const seg = (n: number, color: string, key: string) =>
    n > 0 ? <span key={key} style={{ width: `${(n / total) * 100}%`, background: color }} className="h-full" /> : null;
  const label = `${health.online} online · ${health.alerting} alerting · ${health.offline} offline · ${health.dormant} dormant · ${health.unknown} unknown`;
  return (
    <div role="img" aria-label={label} className={clsx("flex h-1.5 overflow-hidden rounded-full bg-panel-3", className)} title={label}>
      {seg(health.online, STATUS_COLOR.online, "on")}
      {seg(health.alerting, STATUS_COLOR.alerting, "al")}
      {seg(health.offline, STATUS_COLOR.offline, "off")}
      {seg(health.dormant, STATUS_COLOR.dormant, "do")}
      {seg(health.unknown, STATUS_COLOR.unknown, "un")}
    </div>
  );
}

export function Badge({ children, tone = "neutral", className, title }: { children: ReactNode; tone?: "neutral" | "accent" | "warn" | "bad" | "info" | "violet" | "ok"; className?: string; title?: string }) {
  const colors: Record<string, string> = {
    neutral: "bg-panel-3 text-muted",
    accent: "bg-accent-soft text-accent",
    warn: "bg-warn/12 text-warn",
    bad: "bg-bad/12 text-bad",
    info: "bg-info/12 text-info",
    violet: "bg-violet/12 text-violet",
    ok: "bg-ok/12 text-ok",
  };
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium", colors[tone], className)}>
      {children}
    </span>
  );
}

export function Avatar({ name, size = 22 }: { name: string; size?: number }) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  const text = ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white"
      style={{ width: size, height: size, fontSize: size * 0.42, background: `hsl(${h} 55% 42%)` }}
      title={name}
    >
      {text}
    </span>
  );
}

// ---- form controls ------------------------------------------------------------------------------

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { icon?: ReactNode }>(
  function Input({ className, icon, ...rest }, ref) {
    return (
      <div className={clsx("relative", className)}>
        {icon && <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint">{icon}</span>}
        <input
          ref={ref}
          className={clsx(
            "h-8 w-full rounded-lg border border-line bg-panel-2 px-2.5 text-[13px] text-ink placeholder:text-faint",
            "focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20",
            icon && "pl-8",
          )}
          {...rest}
        />
      </div>
    );
  },
);

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={clsx(
        "w-full rounded-lg border border-line bg-panel-2 px-2.5 py-2 text-[13px] text-ink placeholder:text-faint",
        "focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20",
        props.className,
      )}
    />
  );
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        "h-8 rounded-lg border border-line bg-panel-2 px-2 text-[13px] text-ink focus:border-accent/60 focus:outline-none",
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-faint">{hint}</span>}
    </label>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  size = "sm",
}: {
  value: T;
  options: { value: T; label: ReactNode; count?: number }[];
  onChange: (v: T) => void;
  size?: "xs" | "sm";
}) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-panel-2 p-0.5" role="tablist">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "inline-flex items-center gap-1.5 rounded-md font-medium transition-colors",
            size === "sm" ? "h-7 px-2.5 text-[12.5px]" : "h-6 px-2 text-[11.5px]",
            value === o.value ? "bg-panel text-ink shadow-sm" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
          {o.count !== undefined && <span className="num text-[11px] text-faint">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ value, tabs, onChange }: { value: T; tabs: { value: T; label: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="mb-4 flex gap-1 border-b border-line" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={clsx(
            "-mb-px border-b-2 px-3 py-2 text-[13px] font-medium transition-colors",
            value === t.value ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ---- overlays ----------------------------------------------------------------------------------

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  width = 480,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 pt-[10vh] backdrop-blur-[2px]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal
        className="fade-in w-full rounded-2xl border border-line bg-panel shadow-2xl"
        style={{ maxWidth: width }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          <IconButton label="Close" onClick={onClose}>
            <X className="size-4" />
          </IconButton>
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  width = 440,
  subtitle,
  actions,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  width?: number;
  actions?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-40" onMouseDown={onClose}>
      <aside
        className="fade-in absolute right-0 top-0 flex h-full max-w-full flex-col border-l border-line bg-panel shadow-2xl"
        style={{ width }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-[15px] font-semibold">{title}</h2>
            {subtitle && <div className="truncate text-xs text-muted">{subtitle}</div>}
          </div>
          <div className="flex items-center gap-1">
            {actions}
            <IconButton label="Close" onClick={onClose}>
              <X className="size-4" />
            </IconButton>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}

// ---- toasts ------------------------------------------------------------------------------------

type Toast = { id: number; title: string; body?: string; tone: "ok" | "bad" | "info" };
const ToastCtx = createContext<(t: Omit<Toast, "id">) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = nextId.current++;
    setToasts((all) => [...all.slice(-3), { ...t, id }]);
    setTimeout(() => setToasts((all) => all.filter((x) => x.id !== id)), t.tone === "bad" ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      {createPortal(
        <div className="pointer-events-none fixed bottom-4 left-1/2 z-[60] flex w-[min(420px,calc(100vw-32px))] -translate-x-1/2 flex-col gap-2">
          {toasts.map((t) => (
            <div
              key={t.id}
              role="status"
              className="fade-in pointer-events-auto rounded-xl border border-line bg-panel px-4 py-3 shadow-2xl"
              style={{ borderLeft: `3px solid ${t.tone === "ok" ? "var(--ok)" : t.tone === "bad" ? "var(--bad)" : "var(--info)"}` }}
            >
              <p className="text-[13px] font-medium">{t.title}</p>
              {t.body && <p className="mt-0.5 text-xs text-muted">{t.body}</p>}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}

export function Property({ label, children, mono }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-4 border-b border-line py-2 last:border-b-0">
      <dt className="shrink-0 text-[12.5px] text-muted">{label}</dt>
      <dd className={clsx("min-w-0 truncate text-right text-[13px]", mono && "font-mono text-[12px]")}>{children ?? "—"}</dd>
    </div>
  );
}
