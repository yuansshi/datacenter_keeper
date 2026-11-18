import { useSyncExternalStore } from "react";

const KEY = "lk.user";
const listeners = new Set<() => void>();

function read(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

let current = read();

export function getUser(): string {
  return current;
}

export function setUser(name: string): void {
  current = name.trim();
  try {
    localStorage.setItem(KEY, current);
  } catch {
    /* private mode */
  }
  listeners.forEach((l) => l());
}

export function useUser(): string {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => current,
  );
}

export type Theme = "dark" | "light";

export function getTheme(): Theme {
  return (document.documentElement.dataset.theme as Theme) || "dark";
}

const themeListeners = new Set<() => void>();

export function setTheme(t: Theme): void {
  document.documentElement.dataset.theme = t;
  try {
    localStorage.setItem("lk.theme", t);
  } catch {
    /* ignore */
  }
  themeListeners.forEach((l) => l());
}

export function useTheme(): Theme {
  return useSyncExternalStore(
    (cb) => {
      themeListeners.add(cb);
      return () => themeListeners.delete(cb);
    },
    getTheme,
  );
}

export function readPref<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`lk.${key}`);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function writePref(key: string, value: unknown): void {
  try {
    localStorage.setItem(`lk.${key}`, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}
