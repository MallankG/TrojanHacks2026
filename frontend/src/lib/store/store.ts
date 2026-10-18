"use client";

import { useSyncExternalStore } from "react";
import { createInitialState, STATE_VERSION, type DemoState } from "./state";

const STORAGE_KEY = "tracelayer.demo-state";

type Listener = () => void;

const ROLE_IDS = new Set(["analyst", "steward", "officer", "auditor"]);

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Shape check for persisted state, so a corrupt or foreign payload falls back to the seed. */
function isDemoState(value: unknown): value is DemoState {
  if (!isObject(value)) return false;
  const v = value;
  return (
    v.version === STATE_VERSION &&
    typeof v.authed === "boolean" &&
    typeof v.role === "string" &&
    ROLE_IDS.has(v.role) &&
    isObject(v.sim) &&
    isObject(v.data) &&
    isObject(v.graph) &&
    isObject(v.riskQueue) &&
    isObject(v.copilot) &&
    Array.isArray((v.copilot as Record<string, unknown>).messages) &&
    isObject(v.caseWork) &&
    Array.isArray((v.caseWork as Record<string, unknown>).revisions) &&
    ((v.caseWork as Record<string, unknown>).revisions as unknown[]).length > 0 &&
    isObject(v.policy) &&
    Array.isArray(v.events) &&
    Array.isArray(v.exports) &&
    Array.isArray(v.recomputes) &&
    Array.isArray(v.passports) &&
    Array.isArray(v.replays) &&
    Array.isArray(v.sandboxProposals)
  );
}

const SERVER_SNAPSHOT: DemoState = createInitialState();

function loadPersisted(): DemoState | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isDemoState(parsed)) return null;
    // Transient UI never survives a reload.
    return { ...parsed, modal: null, toast: null };
  } catch {
    return null;
  }
}

function persist(state: DemoState): void {
  try {
    const { modal: _modal, toast: _toast, ...rest } = state;
    void _modal;
    void _toast;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(rest));
  } catch {
    // Storage can be unavailable (private mode, quota). The demo still works in memory.
  }
}

let state: DemoState | null = null;
const listeners = new Set<Listener>();

function current(): DemoState {
  if (state === null) {
    state = typeof window === "undefined" ? SERVER_SNAPSHOT : (loadPersisted() ?? createInitialState());
  }
  return state;
}

export function getState(): DemoState {
  return current();
}

/** Applies an immutable update and notifies subscribers. */
export function setState(updater: (prev: DemoState) => DemoState): void {
  const next = updater(current());
  if (next === state) return;
  state = next;
  if (typeof window !== "undefined") persist(next);
  listeners.forEach((listener) => listener());
}

/** Keeps several open tabs consistent: another tab's write replaces this tab's state. */
function onStorage(event: StorageEvent): void {
  if (event.key !== STORAGE_KEY) return;
  const next = loadPersisted() ?? createInitialState();
  state = { ...next, modal: state?.modal ?? null, toast: state?.toast ?? null };
  listeners.forEach((listener) => listener());
}

function subscribe(listener: Listener): () => void {
  if (listeners.size === 0 && typeof window !== "undefined") window.addEventListener("storage", onStorage);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof window !== "undefined") window.removeEventListener("storage", onStorage);
  };
}

/** Subscribes a component to the whole demo state. Derive values with `useMemo`. */
export function useDemoState(): DemoState {
  return useSyncExternalStore(subscribe, current, () => SERVER_SNAPSHOT);
}

const noopSubscribe = () => () => {};

/** `false` during server render and hydration, `true` afterwards. */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
}
