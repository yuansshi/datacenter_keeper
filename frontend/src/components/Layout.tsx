import { useQuery } from "@tanstack/react-query";
import {
  Activity as ActivityIcon,
  CalendarRange,
  FileUp,
  FlaskConical,
  LayoutDashboard,
  Map as MapIcon,
  Menu,
  Moon,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  Rows3,
  Search,
  Server,
  Settings as SettingsIcon,
  Sparkles,
  Sun,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api } from "../lib/api";
import { readPref, setTheme, setUser, useTheme, useUser, writePref } from "../lib/user";
import { Assistant } from "./Assistant";
import { CommandPalette } from "./CommandPalette";
import { Avatar, Badge, Button, Input, Kbd, Modal, clsx } from "./ui";

const NAV: { group: string; items: { to: string; label: string; icon: ReactNode; end?: boolean }[] }[] = [
  {
    group: "Overview",
    items: [{ to: "/", label: "Dashboard", icon: <LayoutDashboard className="size-4" />, end: true }],
  },
  {
    group: "Inventory",
    items: [
      { to: "/devices", label: "Devices", icon: <Server className="size-4" /> },
      { to: "/networks", label: "Networks", icon: <Network className="size-4" /> },
      { to: "/racks", label: "Racks", icon: <Rows3 className="size-4" /> },
      { to: "/floor", label: "Floor plans", icon: <MapIcon className="size-4" /> },
    ],
  },
  {
    group: "Testbeds",
    items: [
      { to: "/testbeds", label: "Testbeds", icon: <FlaskConical className="size-4" /> },
      { to: "/bookings", label: "Bookings", icon: <CalendarRange className="size-4" /> },
    ],
  },
  {
    group: "Data",
    items: [
      { to: "/import", label: "Testbed files", icon: <FileUp className="size-4" /> },
      { to: "/activity", label: "Activity", icon: <ActivityIcon className="size-4" /> },
      { to: "/settings", label: "Settings", icon: <SettingsIcon className="size-4" /> },
    ],
  },
];

function Logo({ compact }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden>
        <rect width="32" height="32" rx="8" fill="var(--accent-soft)" />
        <rect x="8" y="6" width="16" height="4" rx="1.2" fill="var(--accent)" />
        <rect x="8" y="12" width="16" height="4" rx="1.2" fill="var(--accent)" opacity=".7" />
        <rect x="8" y="18" width="16" height="4" rx="1.2" fill="var(--accent)" opacity=".45" />
        <circle cx="21.5" cy="25" r="2" fill="var(--accent)" />
      </svg>
      {!compact && (
        <span className="text-[15px] font-semibold tracking-tight">
          lab<span className="text-accent">keeper</span>
        </span>
      )}
    </div>
  );
}

function Sidebar({ collapsed, onToggle, onNavigate }: { collapsed: boolean; onToggle: () => void; onNavigate?: () => void }) {
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 15_000 });
  const counts: Record<string, number | undefined> = {
    "/devices": stats?.devices.total,
    "/testbeds": stats?.testbeds.total,
    "/racks": stats?.racks,
    "/networks": stats?.networks,
    "/floor": stats?.floor_plans,
  };
  return (
    <nav className={clsx("flex h-full flex-col border-r border-line bg-panel", collapsed ? "w-[60px]" : "w-[232px]")}>
      <div className={clsx("flex h-14 items-center border-b border-line", collapsed ? "justify-center" : "justify-between px-4")}>
        <Logo compact={collapsed} />
        {!collapsed && (
          <button className="hidden text-faint hover:text-ink lg:block" onClick={onToggle} aria-label="Collapse sidebar">
            <PanelLeftClose className="size-4" />
          </button>
        )}
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-3">
        {NAV.map((g) => (
          <div key={g.group} className="mb-4">
            {!collapsed && <p className="mb-1 px-2 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-faint">{g.group}</p>}
            {g.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={onNavigate}
                title={collapsed ? item.label : undefined}
                className={({ isActive }) =>
                  clsx(
                    "group mb-0.5 flex h-8 items-center gap-2.5 rounded-lg text-[13px] font-medium transition-colors",
                    collapsed ? "justify-center" : "px-2",
                    isActive ? "bg-accent-soft text-accent" : "text-muted hover:bg-panel-2 hover:text-ink",
                  )
                }
              >
                {item.icon}
                {!collapsed && <span className="flex-1">{item.label}</span>}
                {!collapsed && counts[item.to] !== undefined && (
                  <span className="num text-[11px] text-faint group-hover:text-muted">{counts[item.to]}</span>
                )}
              </NavLink>
            ))}
          </div>
        ))}
      </div>
      {collapsed && (
        <button className="mx-auto mb-3 hidden text-faint hover:text-ink lg:block" onClick={onToggle} aria-label="Expand sidebar">
          <PanelLeftOpen className="size-4" />
        </button>
      )}
    </nav>
  );
}

function IdentityPrompt() {
  const user = useUser();
  const [name, setName] = useState("");
  const { data: system } = useQuery({ queryKey: ["system"], queryFn: api.system });
  const open = !user;
  return (
    <Modal
      open={open}
      onClose={() => name.trim() && setUser(name)}
      title="Welcome to Labkeeper"
      footer={
        <Button variant="primary" disabled={!name.trim()} onClick={() => setUser(name)}>
          Continue
        </Button>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) setUser(name);
        }}
      >
        <p className="mb-3 text-[13px] text-muted">
          Bookings are made under your name. Use the same name your team knows you by (your username works well).
        </p>
        <Input autoFocus placeholder="e.g. jdoe" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
        {system?.demo && (
          <p className="mt-3 text-xs text-faint">
            This is demo data: alice, bob, carol, dave and erin already hold some bookings.
          </p>
        )}
      </form>
    </Modal>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(() => readPref("sidebar.collapsed", false));
  const [mobileNav, setMobileNav] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(() => readPref("assistant.open", false));
  const theme = useTheme();
  const user = useUser();
  const location = useLocation();
  const { data: system } = useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 30_000 });

  useEffect(() => writePref("sidebar.collapsed", collapsed), [collapsed]);
  useEffect(() => writePref("assistant.open", assistantOpen), [assistantOpen]);
  useEffect(() => setMobileNav(false), [location.pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      } else if (mod && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setAssistantOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    const openAssistant = () => setAssistantOpen(true);
    window.addEventListener("lk:assistant", openAssistant);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("lk:assistant", openAssistant);
    };
  }, []);

  return (
    <div className="flex h-full">
      <div className="hidden lg:block">
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((v) => !v)} />
      </div>
      {mobileNav && (
        <div className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={() => setMobileNav(false)}>
          <div className="h-full w-[232px]" onClick={(e) => e.stopPropagation()}>
            <Sidebar collapsed={false} onToggle={() => {}} onNavigate={() => setMobileNav(false)} />
          </div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-panel/80 px-3 backdrop-blur sm:px-4">
          <button className="text-muted lg:hidden" onClick={() => setMobileNav(true)} aria-label="Open menu">
            <Menu className="size-5" />
          </button>
          <button
            onClick={() => setPaletteOpen(true)}
            className="flex h-9 w-full min-w-0 max-w-md items-center gap-2 rounded-lg border border-line bg-panel-2 px-3 text-left text-[13px] text-faint transition-colors hover:border-line-strong"
          >
            <Search className="size-4" />
            <span className="flex-1 truncate">Search devices, serials, testbeds, racks…</span>
            <span className="hidden sm:inline">
              <Kbd>⌘K</Kbd>
            </span>
          </button>
          <div className="flex-1" />
          {system?.demo && (
            <Badge tone="violet" className="hidden sm:inline-flex">
              Demo data
            </Badge>
          )}
          <Button
            variant={assistantOpen ? "primary" : "secondary"}
            icon={<Sparkles className="size-4" />}
            onClick={() => setAssistantOpen((v) => !v)}
            title="Assistant (⌘J)"
          >
            <span className="hidden sm:inline">Ask Labkeeper</span>
          </Button>
          <button
            className="inline-flex size-9 items-center justify-center rounded-lg text-muted hover:bg-panel-2 hover:text-ink"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label="Toggle theme"
            title="Toggle theme"
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
          {user && (
            <NavLink to="/settings" className="flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-panel-2" title={`Signed in as ${user}`}>
              <Avatar name={user} size={26} />
              <span className="hidden text-[13px] font-medium md:inline">{user}</span>
            </NavLink>
          )}
        </header>
        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-[1440px] px-4 py-5 sm:px-6">{children}</div>
          </main>
          {assistantOpen && <Assistant onClose={() => setAssistantOpen(false)} />}
        </div>
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <IdentityPrompt />
    </div>
  );
}
