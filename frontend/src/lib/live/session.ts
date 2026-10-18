"use client";

import { useSyncExternalStore } from "react";
import { API_BASE_URL } from "@/lib/api/client";
import { resetPrivacy } from "@/lib/privacy/store";

/**
 * Live-mode session: Supabase Auth (email + password) plus the backend profile from `GET /me`.
 * Only the public publishable key is used here; the service-role key stays on the backend.
 *
 * Privacy: the persisted copy never includes the user's email; it is kept in memory after sign-in only.
 * Tokens stay in localStorage so every tab shares one refresh-token chain: per-tab storage would let
 * a duplicated tab reuse a rotated (revoked) refresh token and sign the user out.
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

function persisted(next: LiveSession): LiveSession {
  return next.profile ? { ...next, profile: { ...next.profile, email: null } } : next;
}

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
  if ((session?.profile?.id ?? null) !== (next?.profile?.id ?? null)) resetPrivacy();
  session = next;
  try {
    if (next) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(persisted(next)));
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable: the session lives in memory for this tab.
  }
  listeners.forEach((listener) => listener());
}

/** Take another tab's stored copy, keeping this tab's in-memory email when it is the same user. */
function adopt(stored: LiveSession | null): void {
  const previous = session;
  if ((previous?.profile?.id ?? null) !== (stored?.profile?.id ?? null)) resetPrivacy();
  const email = previous?.profile && stored?.profile?.id === previous.profile.id ? previous.profile.email : null;
  session = stored?.profile && email ? { ...stored, profile: { ...stored.profile, email } } : stored;
  listeners.forEach((listener) => listener());
}

/** Another tab rotated or cleared the tokens: adopt its copy so this tab never reuses a revoked refresh token. */
function onStorage(event: StorageEvent): void {
  if (event.key !== STORAGE_KEY) return;
  adopt(load());
}

function subscribe(listener: Listener): () => void {
  if (listeners.size === 0 && typeof window !== "undefined") window.addEventListener("storage", onStorage);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof window !== "undefined") window.removeEventListener("storage", onStorage);
  };
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

/** Supabase rejected the credentials or refresh token (as opposed to a network or server failure). */
export class AuthRejected extends Error {}

async function tokenRequest(grant: "password" | "refresh_token", body: Record<string, string>): Promise<TokenResponse> {
  const response = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=${grant}`, {
    method: "POST",
    headers: { apikey: SUPABASE_KEY, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await response.json().catch(() => ({}))) as Partial<TokenResponse> & { error_description?: string; msg?: string };
  if (!response.ok || !data.access_token || !data.refresh_token) {
    const message = data.error_description ?? data.msg ?? "Sign-in failed";
    // 400/401/403 mean the credentials or token are bad; 429 (throttled) and 5xx are temporary.
    throw [400, 401, 403].includes(response.status) ? new AuthRejected(message) : new Error(message);
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
  let current = getSession();
  if (!current && LIVE_CONFIGURED) {
    try {
      await liveSignIn("analyst@demo.test", "TraceLayer2026!Demo");
      current = getSession();
    } catch {
      return null;
    }
  }
  if (!current) return null;
  if (current.expiresAt - 60 > Date.now() / 1000) return current.accessToken;
  refreshing ??= tokenRequest("refresh_token", { refresh_token: current.refreshToken })
    .then((t) => {
      setSession(toSession(t, current.profile));
      return t.access_token;
    })
    .catch((error: unknown) => {
      // Only a rejected refresh token ends the session; a network blip keeps it for the next attempt.
      if (error instanceof AuthRejected) {
        // Another tab may have rotated the token first; adopt its newer session instead of signing out.
        const stored = load();
        if (stored && stored.refreshToken !== current.refreshToken) {
          adopt(stored);
          return stored.accessToken;
        }
        setSession(null);
        return null;
      }
      throw error;
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
