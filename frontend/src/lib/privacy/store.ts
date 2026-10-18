"use client";

import { useCallback, useSyncExternalStore } from "react";
import { maskRecord, maskRef, safeText } from "./pii";

/**
 * Identifier masking state. Masked by default and held in memory only, so a reload or a new
 * identity always starts masked. A single reveal lasts REVEAL_MS, then the value is masked again.
 */

export const REVEAL_MS = 30_000;

interface PrivacyState {
  masked: boolean;
  revealed: ReadonlySet<string>;
}

const INITIAL: PrivacyState = { masked: true, revealed: new Set() };
let state: PrivacyState = INITIAL;
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const listeners = new Set<() => void>();

function emit(next: PrivacyState): void {
  state = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const getState = () => state;
const getServerState = () => INITIAL;

export function usePrivacy(): PrivacyState {
  return useSyncExternalStore(subscribe, getState, getServerState);
}

export function setMasked(masked: boolean): void {
  emit({ ...state, masked });
}

/** Show one value in full for REVEAL_MS. */
export function reveal(value: string): void {
  clearTimeout(timers.get(value));
  timers.set(value, setTimeout(() => conceal(value), REVEAL_MS));
  emit({ ...state, revealed: new Set(state.revealed).add(value) });
}

export function conceal(value: string): void {
  clearTimeout(timers.get(value));
  timers.delete(value);
  const revealed = new Set(state.revealed);
  revealed.delete(value);
  emit({ ...state, revealed });
}

/** Sign-in, sign-out and identity changes start over fully masked. */
export function resetPrivacy(): void {
  timers.forEach(clearTimeout);
  timers.clear();
  emit(INITIAL);
}

/**
 * Formatters for string-only places (aria-labels, graph labels, titles). They follow the global
 * toggle and per-value reveals, so a revealed account shows in full everywhere on the screen.
 */
export function useMaskers() {
  const { masked, revealed } = usePrivacy();
  const ref = useCallback(
    (value: string | null | undefined) => (masked && !(value && revealed.has(value)) ? maskRef(value) : (value ?? "Not recorded")),
    [masked, revealed],
  );
  const text = useCallback((value: string | null | undefined) => safeText(value, masked, revealed), [masked, revealed]);
  const record = useCallback((value: Record<string, unknown> | null | undefined) => maskRecord(value, masked), [masked]);
  return { masked, ref, text, record };
}
