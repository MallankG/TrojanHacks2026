"use client";

import { useSyncExternalStore } from "react";
import { API_BASE_URL } from "@/lib/api/client";

/**
 * Live-mode session: Supabase Auth (email + password) plus the backend profile from `GET /me`.
 * Only the public publishable key is used here; the service-role key stays on the backend.
 */

export const SUPABASE_URL = (process.env.NEXT_PUBLIC_SUPABASE_URL ?? "").replace(/\/$/, "");
const SUPABASE_KEY = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "";

/** Live mode needs the backend and Supabase Auth configured. */
export const LIVE_CONFIGURED = Boolean(API_BASE_URL && SUPABASE_URL && SUPABASE_KEY);

export type BackendRole = "admin" | "investigator" | "analyst" | "reviewer" | "data_steward";

export interface LiveProfile {
  id: string;
  email: string | null;
  display_name: string | null;
  role: BackendRole;
  tenant: { id: string; slug: string; name: string; is_synthetic: boolean } | null;
}

export interface LiveSession {
  accessToken: string;
  refreshToken: string;
  /** Epoch seconds. */
  expiresAt: number;
  profile: LiveProfile | null;
}

const STORAGE_KEY = "tracelayer.live-session";

type Listener = () => void;
const listeners = new Set<Listener>();
let session: LiveSession | null | undefined;

function load(): LiveSession | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as LiveSession) : null;
  } catch {
    return null;
  }
}

export function getSession(): LiveSession | null {
  if (session === undefined) session = typeof window === "undefined" ? null : load();
  return session;
}

function setSession(next: LiveSession | null): void {
  session = next;
  try {
    if (next) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable: the session lives in memory for this tab.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useLiveSession(): LiveSession | null {
  return useSyncExternalStore(subscribe, getSession, () => null);
}

/** The signed-in backend profile, or `null` when the app is running on seeded demo data only. */
export function useLiveProfile(): LiveProfile | null {
  return useLiveSession()?.profile ?? null;
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  expires_at?: number;
}

async function tokenRequest(grant: "password" | "refresh_token", body: Record<string, string>): Promise<TokenResponse> {
  const response = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=${grant}`, {
    method: "POST",
    headers: { apikey: SUPABASE_KEY, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await response.json().catch(() => ({}))) as Partial<TokenResponse> & { error_description?: string; msg?: string };
  if (!response.ok || !data.access_token || !data.refresh_token) {
    throw new Error(data.error_description ?? data.msg ?? "Sign-in failed");
  }
  return data as TokenResponse;
}

function toSession(t: TokenResponse, profile: LiveProfile | null): LiveSession {
  return {
    accessToken: t.access_token,
    refreshToken: t.refresh_token,
    expiresAt: t.expires_at ?? Math.floor(Date.now() / 1000) + t.expires_in,
    profile,
  };
}

async function fetchProfile(accessToken: string): Promise<LiveProfile> {
  const response = await fetch(`${API_BASE_URL}/me`, { headers: { Authorization: `Bearer ${accessToken}` }, cache: "no-store" });
  if (response.status === 403) throw new Error("Your account is not provisioned for a tenant yet. Ask an admin to assign you a role.");
  if (!response.ok) throw new Error(`Could not load your profile (status ${response.status}).`);
  return (await response.json()) as LiveProfile;
}

export async function liveSignIn(email: string, password: string): Promise<LiveProfile> {
  const tokens = await tokenRequest("password", { email, password });
  const profile = await fetchProfile(tokens.access_token);
  setSession(toSession(tokens, profile));
  return profile;
}

let refreshing: Promise<string | null> | null = null;

/** Returns a valid access token, refreshing it shortly before expiry. `null` means signed out. */
export async function getAccessToken(): Promise<string | null> {
  const current = getSession();
  if (!current) return null;
  if (current.expiresAt - 60 > Date.now() / 1000) return current.accessToken;
  refreshing ??= tokenRequest("refresh_token", { refresh_token: current.refreshToken })
    .then((t) => {
      setSession(toSession(t, current.profile));
      return t.access_token;
    })
    .catch(() => {
      setSession(null);
      return null;
    })
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

export async function liveSignOut(): Promise<void> {
  const current = getSession();
  setSession(null);
  if (!current) return;
  try {
    await fetch(`${SUPABASE_URL}/auth/v1/logout`, {
      method: "POST",
      headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${current.accessToken}` },
    });
  } catch {
    // The local session is already cleared.
  }
}
