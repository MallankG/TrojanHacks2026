"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useBackendHealth } from "@/lib/api/use-backend-health";
import { cn } from "@/lib/cn";
import { call, useLive } from "@/lib/live/api";
import { useDashboardSummary } from "@/lib/live/api-command";
import { useLiveProfile } from "@/lib/live/session";
import { NAV_GROUPS, ROUTES, type NavItem } from "@/lib/routes";
import { openModal, resetDemo, signOut, toggleSimulation } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { roleInitials } from "@/lib/seed/roles";
import type { Tone } from "@/lib/types";
import { Icon } from "@/components/ui/icon";
import { Pill } from "@/components/ui/pill";
import { LogoMark } from "./logo";

const SIDE_BUTTON = "min-h-9 w-full rounded-lg border border-sidebar-line px-2.5 text-left text-[12.5px] font-semibold text-[#c5d0de] hover:bg-sidebar-hover hover:text-white";

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const state = useDemoState();
  const role = useRole();
  const backend = useBackendHealth();
  const profile = useLiveProfile();
  const graphHealth = useLive(() => profile
    ? call<{ status: string }>("/graph/engine/health")
    : Promise.resolve(null), "graph-engine-health");
  const fixed = state.data.driftFixed;
  const pendingForOfficer = state.caseWork.status === "Pending approval" && role.canApproveEscalation;

  // Live session: badges and the ingestion/scoring pills come from the dashboard summary.
  const summary = useDashboardSummary().data;
  const count = (n: number | undefined) => (n ? String(n) : null);

  const badge = (item: NavItem): string | null => {
    if (profile) {
      if (item.screen === "data") return count(summary?.ingestion.open_issues_total);
      if (item.screen === "risk") return count(summary?.alerts.open_total);
      if (item.screen === "cases") return count(summary?.cases?.pending_review);
      return null;
    }
    if (item.screen === "data" && !fixed) return "1";
    if (item.screen === "cases" && (!state.caseWork.claimFixed || pendingForOfficer)) return "1";
    if (item.screen === "risk") return "7";
    return null;
  };

  const latestRun = summary?.ingestion.latest_run;
  const ranker = summary?.alerts.model_run;
  const health: Array<{ label: string; status: string; tone: Tone }> = profile
    ? [
        {
          label: "Ingestion",
          status: !summary ? "Checking" : !latestRun ? "No uploads yet" : summary.ingestion.failed_runs ? "Last run failed" : `${summary.ingestion.open_issues_total} open issues`,
          tone: !latestRun ? "mute" : summary?.ingestion.failed_runs ? "bad" : summary?.ingestion.open_issues_total ? "warn" : "ok",
        },
        { label: "GraphBase", status: graphHealth.loading ? "Checking" : graphHealth.data?.status === "ok" ? "Engine ready" : "Unavailable", tone: graphHealth.data?.status === "ok" ? "ok" : graphHealth.error ? "bad" : "mute" },
        { label: "Scoring", status: !summary ? "Checking" : ranker ? `Ranked ${ranker.completed_at ? ranker.completed_at.slice(5, 16).replace("T", " ") : ""}` : "Not run yet", tone: ranker ? "ok" : "mute" },
        { label: "LLM", status: "Not connected (agents)", tone: "mute" },
      ]
    : [
        { label: "Ingestion", status: fixed ? "Cash feed late" : "1 batch on hold", tone: "warn" },
        { label: "GraphBase", status: state.sim.graph ? "Down, snapshots only" : "Healthy, lag 42 s", tone: state.sim.graph ? "bad" : "ok" },
        { label: "Scoring", status: "Healthy, 18 min ago", tone: "ok" },
        { label: "LLM", status: state.sim.llm ? "Down, drafting paused" : "Healthy, Groq", tone: state.sim.llm ? "bad" : "ok" },
      ];
  health.push(
    {
      label: "API",
      status:
        backend === "online" ? (profile ? "Live data" : "Connected, demo sign-in") : backend === "checking" ? "Checking" : backend === "offline" ? (profile ? "Offline" : "Offline, demo data") : "Demo data",
      tone: backend === "online" ? "ok" : backend === "offline" ? (profile ? "bad" : "warn") : "mute",
    });

  return (
    <aside className="flex w-full flex-col gap-1 bg-sidebar px-3.5 py-5 lg:sticky lg:top-0 lg:h-screen lg:w-64 lg:shrink-0 lg:overflow-y-auto">
      <Link href={ROUTES.command} className="mb-1.5 ml-1.5 flex items-center gap-2.5">
        <LogoMark />
        <div>
          <div className="text-[19px] font-bold tracking-[-0.01em] text-white">TraceLayer</div>
          <div className="text-[11.5px] text-sidebar-muted">Governed investigation layer</div>
        </div>
      </Link>
      <div className="mx-1 mb-0.5 mt-2 rounded-[10px] border border-sidebar-line px-2.5 py-2">
        <div className="text-[11px] text-sidebar-muted">Tenant</div>
        <div className="text-[13px] font-semibold text-white">{profile?.tenant?.name ?? "Lakeview Community Bank"}</div>
        <div className="text-[11px] text-attention">Synthetic data only</div>
      </div>

      <nav aria-label="Primary">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            <div className="mb-1 ml-3 mt-3.5 font-mono text-[10.5px] uppercase tracking-[0.16em] text-sidebar-muted">{group.label}</div>
            <ul className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const active = pathname === item.match || pathname.startsWith(`${item.match}/`);
                const count = badge(item);
                const locked = item.caseScoped && !role.canAccessCases;
                return (
                  <li key={item.screen}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex min-h-[42px] items-center gap-2.5 rounded-[10px] px-3 text-sm font-medium transition-colors",
                        active ? "bg-sidebar-active text-white shadow-[inset_3px_0_0_var(--color-brand-light)]" : "text-sidebar-text hover:bg-sidebar-hover hover:text-white",
                      )}
                    >
                      <Icon name={item.icon} />
                      <span className="flex-1">{item.label}</span>
                      {locked ? <Pill tone="mute">No access</Pill> : null}
                      {count && !locked ? <span className="rounded-full bg-attention px-2 text-xs font-semibold text-attention-dark">{count}</span> : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="mb-1 ml-3 mt-3.5 font-mono text-[10.5px] uppercase tracking-[0.16em] text-sidebar-muted">System health</div>
      <ul className="flex flex-col gap-1.5 px-1.5" aria-label="System health">
        {health.map((h) => (
          <li key={h.label} className="flex items-center justify-between gap-2 text-[12.5px] text-[#c5d0de]">
            <span>{h.label}</span>
            <Pill tone={h.tone} className="px-2 text-[11px]">
              {h.status}
            </Pill>
          </li>
        ))}
      </ul>

      <div className="min-h-3 flex-1" />
      {!profile && <div className="flex flex-col gap-1.5 rounded-xl border border-dashed border-[#34506f] p-2.5">
        <div className="font-mono text-[10.5px] tracking-[0.14em] text-sidebar-muted">PROTOTYPE CONTROLS</div>
        <button type="button" className={SIDE_BUTTON} onClick={() => toggleSimulation("llm")}>
          {state.sim.llm ? "Restore LLM" : "Simulate LLM outage"}
        </button>
        <button type="button" className={SIDE_BUTTON} onClick={() => toggleSimulation("graph")}>
          {state.sim.graph ? "Restore GraphBase" : "Simulate GraphBase outage"}
        </button>
        <button type="button" className={SIDE_BUTTON} onClick={() => openModal({ kind: "sessionTimeout" })}>
          Simulate session timeout
        </button>
        <button
          type="button"
          className={SIDE_BUTTON}
          onClick={() => {
            resetDemo();
            router.push(ROUTES.command);
          }}
        >
          Reset demo
        </button>
      </div>}
      <div className="mt-2.5 border-t border-[#1e3350] px-1 pt-3 pb-3">
        <div className="flex items-center gap-2.5 px-1 py-1">
          <span aria-hidden="true" className="inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-sidebar-active text-xs font-bold text-brand-light">
            {roleInitials(role)}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-semibold text-white" title={role.name}>
              {role.name}
            </div>
            <div className="truncate text-[11.5px] text-sidebar-muted">
              {role.label}
            </div>
          </div>
        </div>
        <button
          type="button"
          className="mt-2.5 flex h-8.5 w-full items-center justify-center gap-2 rounded-lg border border-sidebar-line bg-[#0d1d33] px-3 text-[12px] font-medium text-[#c5d0de] transition-colors hover:border-red-900/60 hover:bg-red-950/40 hover:text-red-300"
          onClick={() => {
            signOut();
            router.push(ROUTES.signIn);
          }}
        >
          <svg className="size-3.5 shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" />
          </svg>
          Sign out
        </button>
      </div>
    </aside>
  );
}
