"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ROUTES, SCREEN_TITLES, screenForPath } from "@/lib/routes";
import { openModal, selectBatch, setRole, signOut, updateGraph } from "@/lib/store/actions";
import { useLive } from "@/lib/live/api";
import { commandApi, useLiveNotifications, type SearchHit } from "@/lib/live/api-command";
import { utc } from "@/lib/live/format";
import { LIVE_ROLES } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { setMasked, useMaskers } from "@/lib/privacy/store";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { ROLE_ORDER, ROLES } from "@/lib/seed/roles";
import type { RoleId } from "@/lib/types";
import { buttonClasses } from "@/components/ui/button";
import { Icon } from "@/components/ui/icon";
import { Pill } from "@/components/ui/pill";
import { useCanReveal } from "@/components/ui/sensitive";

interface SearchEntry {
  id: string;
  description: string;
  go: () => void;
  /** Case records are hidden from roles without case scope. */
  caseScoped?: boolean;
}

/** Closes a popover when the user clicks outside it or navigates. */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  const pathname = usePathname();
  const closeRef = useRef(close);
  useEffect(() => {
    closeRef.current = close;
  }, [close]);
  useEffect(() => {
    closeRef.current();
  }, [pathname]);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) closeRef.current();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return ref;
}

function GlobalSearch() {
  const router = useRouter();
  const state = useDemoState();
  const role = useRole();
  const mask = useMaskers();
  const [query, setQuery] = useState("");
  const ref = useDismiss(query !== "", () => setQuery(""));
  const fixed = state.data.driftFixed;

  const index = useMemo<SearchEntry[]>(
    () => [
      { id: "AL-1027", caseScoped: true, description: "Alert · Layering, rapid pass-through", go: () => router.push(ROUTES.riskAlert("AL-1027")) },
      { id: "AL-1029", caseScoped: true, description: "Alert · Shared-device mule cluster", go: () => router.push(ROUTES.riskAlert("AL-1029")) },
      { id: "AL-1033", caseScoped: true, description: "Alert · High-volume payroll lookalike", go: () => router.push(ROUTES.riskAlert("AL-1033")) },
      { id: "CASE-0412", caseScoped: true, description: `Case · ${state.caseWork.status}`, go: () => router.push(ROUTES.case("CASE-0412")) },
      { id: "CASE-0398", caseScoped: true, description: "Case · Closed, no further action", go: () => router.push(ROUTES.case("CASE-0398")) },
      { id: "ACC-2208", caseScoped: true, description: "Account · Seed of CASE-0412", go: () => router.push(ROUTES.entity("ACC-2208")) },
      { id: "ACC-3315", caseScoped: true, description: "Account · Intermediary", go: () => router.push(ROUTES.entity("ACC-3315")) },
      { id: "ACC-3391", caseScoped: true, description: "Account · Shares device DV-77", go: () => router.push(ROUTES.entity("ACC-3391")) },
      {
        id: "TX-88237",
        caseScoped: true,
        description: "Transaction · ACC-3315 to ACC-4471",
        go: () => {
          updateGraph({ selection: { kind: "edge", key: "e3" } });
          router.push(ROUTES.graph);
        },
      },
      {
        id: "TX-88266",
        caseScoped: true,
        description: "Transaction · Cross-border wire",
        go: () => {
          updateGraph({ selection: { kind: "edge", key: "e5" } });
          router.push(ROUTES.graph);
        },
      },
      {
        id: "ach_2026_10_06_b",
        description: `Batch · ${fixed ? "Loaded after fix" : "Quarantined"}`,
        go: () => {
          selectBatch("ach_2026_10_06_b");
          router.push(ROUTES.data);
        },
      },
      { id: "xgb-aml", description: "Model · v1.4.2 champion", go: () => router.push(ROUTES.governance("models")) },
    ],
    [router, state.caseWork.status, fixed],
  );

  const q = query.trim().toLowerCase();
  const live = useLiveProfile() !== null;
  const liveQuery = live && q.length >= 2 ? q : "";
  const liveHits = useLive(async () => (liveQuery ? await commandApi.search(liveQuery) : null), `search:${liveQuery}`);
  const hrefFor = (hit: SearchHit) =>
    hit.type === "alert" ? ROUTES.riskAlert(hit.id) : hit.type === "case" ? ROUTES.case(hit.id) : ROUTES.entity(hit.type === "account" ? hit.id : (hit.account_id ?? hit.id));
  const liveEntries: SearchEntry[] = liveHits.data
    ? [...liveHits.data.results.alerts, ...liveHits.data.results.cases, ...liveHits.data.results.accounts, ...liveHits.data.results.transactions]
        .slice(0, 8)
        .map((hit) => ({ id: hit.label, description: `Live · ${hit.description}`, go: () => router.push(hrefFor(hit)) }))
    : [];
  const results = q
    ? [...liveEntries, ...index.filter((entry) => (role.canAccessCases || !entry.caseScoped) && `${entry.id} ${entry.description}`.toLowerCase().includes(q))].slice(0, live ? 10 : 6)
    : [];

  return (
    <div ref={ref} className="relative min-w-0 max-w-[380px] flex-[1_1_260px]">
      <label className="flex items-center gap-2 rounded-[10px] border border-line-strong bg-hover px-3">
        <Icon name="search" size={17} className="text-muted" />
        <span className="sr-only">Search alerts, cases, accounts and transactions</span>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search AL-, CASE-, ACC-, TX-"
          className="min-h-10 min-w-0 flex-1 bg-transparent py-2 text-sm outline-none placeholder:text-[#7b8698]"
          type="search"
          aria-describedby="global-search-hint"
        />
      </label>
      <span id="global-search-hint" className="sr-only">
        Results appear below as you type.
      </span>
      {q ? (
        <div role="region" aria-label="Search results" aria-live="polite" className="absolute left-0 right-0 top-[calc(100%+6px)] z-40 flex flex-col rounded-xl border border-line bg-white p-1.5 shadow-[0_14px_40px_rgba(11,22,38,0.18)]">
          {results.map((entry) => (
            <button
              key={entry.id}
              type="button"
              className="flex min-h-11 w-full flex-col rounded-lg px-2.5 py-2 text-left text-[13.5px] hover:bg-canvas"
              onClick={() => {
                entry.go();
                setQuery("");
              }}
            >
              <b className="font-mono text-[12.5px]">{mask.text(entry.id)}</b>
              <span className="text-xs text-muted">{mask.text(entry.description)}</span>
            </button>
          ))}
          {results.length === 0 ? (
            <div className="p-3 text-[13.5px]">
              <b>No matches in your scope.</b>
              <br />
              <span className="text-xs text-muted">Results are limited to records your role can see.</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Notifications() {
  const router = useRouter();
  const state = useDemoState();
  const role = useRole();
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));

  const liveNotes = useLiveNotifications();
  const items: Array<{ at: string; text: string; href: string }> = [];
  if (liveNotes.live) {
    for (const n of liveNotes.data?.items ?? []) {
      const href =
        n.target_type === "case" && n.target_id ? ROUTES.case(n.target_id) : n.target_type === "cases" ? ROUTES.cases : n.target_type === "alerts" ? ROUTES.risk : n.target_type === "data_quality" ? ROUTES.dataQuality : ROUTES.data;
      items.push({ at: n.at ? utc(n.at).replace(" UTC", "") : "Now", text: n.detail ? `${n.title} ${n.detail}` : n.title, href });
    }
  } else if (!state.data.driftFixed) items.push({ at: "13:20", text: "ach_2026_10_06_b quarantined. You are the backup owner.", href: ROUTES.data });
  if (!liveNotes.live && !state.caseWork.claimFixed) items.push({ at: "13:52", text: "Verifier blocked 1 claim in CASE-0412.", href: ROUTES.case("CASE-0412", "narrative") });
  if (!liveNotes.live && state.caseWork.status === "Pending approval" && role.canApproveEscalation) items.push({ at: "Now", text: "Approval requested: CASE-0412 escalation.", href: ROUTES.case("CASE-0412", "disposition") });
  if (!liveNotes.live) {
    items.push({ at: "13:41", text: "AL-1027 and AL-1029 clustered as CL-17.", href: ROUTES.riskAlert("AL-1027") });
    items.push({ at: "12:58", text: "Cash feed is late against its 2-hour SLA.", href: ROUTES.data });
  }

  return (
    <div ref={ref} className="relative">
      <button type="button" className={buttonClasses("ghost", "sm")} aria-label={`Notifications, ${items.length} items`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="bell" size={17} />
        <span className="rounded-full bg-attention px-1.5 text-xs font-semibold text-attention-dark">{items.length}</span>
      </button>
      {open ? (
        <div className="absolute right-0 top-[calc(100%+6px)] z-40 flex w-[340px] flex-col rounded-xl border border-line bg-white p-1.5 shadow-[0_14px_40px_rgba(11,22,38,0.18)]">
          <div className="px-2.5 py-2 text-[13.5px] font-semibold">Notifications</div>
          {items.map((item) => (
            <button
              key={`${item.at}-${item.text}`}
              type="button"
              className="flex min-h-11 w-full flex-col rounded-lg px-2.5 py-2 text-left text-[13.5px] hover:bg-canvas"
              onClick={() => {
                setOpen(false);
                router.push(item.href);
              }}
            >
              <span className="text-xs text-muted">{item.at} UTC</span>
              <span>{item.text}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Account identifiers are masked by default; the toggle is in memory only and resets on reload or sign-in. */
function MaskToggle() {
  const { masked } = useMaskers();
  const canReveal = useCanReveal();
  // Switching to a role without reveal rights re-masks immediately.
  useEffect(() => {
    if (!canReveal && !masked) setMasked(true);
  }, [canReveal, masked]);
  return (
    <button
      type="button"
      className={buttonClasses("ghost", "sm")}
      aria-pressed={masked}
      disabled={!canReveal}
      title={!canReveal ? "Your role sees masked identifiers only." : masked ? "Account identifiers are masked. Click to show them in full." : "Account identifiers are shown in full. Click to mask them."}
      onClick={() => setMasked(!masked)}
    >
      <Icon name={masked ? "eyeOff" : "eye"} size={16} />
      {masked ? "IDs masked" : "IDs visible"}
    </button>
  );
}

export function Topbar() {
  const router = useRouter();
  const pathname = usePathname();
  const state = useDemoState();
  const profile = useLiveProfile();
  const screen = SCREEN_TITLES[screenForPath(pathname)];
  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-line bg-white px-7 py-3">
      <div className="min-w-0 flex-[1_1_240px]">
        <div className="eyebrow">{screen.eyebrow}</div>
        <div className="text-lg font-semibold">{screen.title}</div>
      </div>
      <GlobalSearch />
      <Pill tone="warn">Synthetic data</Pill>
      <MaskToggle />
      {profile ? (
        // Live: the account's own role (several backend roles share one prototype role, so the picker would mislabel it)
        <span className="flex items-center gap-2 text-[12.5px] text-muted" title="Your role comes from your account">
          Role
          <span className="flex min-h-10 items-center rounded-[10px] border border-line bg-subtle px-2.5 text-sm text-ink">{LIVE_ROLES[profile.role].label}</span>
        </span>
      ) : (
        <label className="flex items-center gap-2 text-[12.5px] text-muted">
          Role
          <select
            value={state.role}
            onChange={(event) => setRole(event.target.value as RoleId)}
            className="min-h-10 rounded-[10px] border border-[#b9c3d1] bg-white px-2.5 py-1.5 text-sm text-ink"
          >
            {ROLE_ORDER.map((id) => (
              <option key={id} value={id}>
                {ROLES[id].label}
              </option>
            ))}
          </select>
        </label>
      )}
      <button type="button" className={buttonClasses("ghost", "sm")} onClick={() => openModal({ kind: "help" })}>
        Help
      </button>
      <Notifications />
      <button
        type="button"
        className={buttonClasses("ghost", "sm")}
        onClick={() => {
          signOut();
          router.push(ROUTES.signIn);
        }}
      >
        Sign out
      </button>
    </header>
  );
}
