"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useBackendHealth } from "@/lib/api/use-backend-health";
import { cn } from "@/lib/cn";
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
  const fixed = state.data.driftFixed;
  const pendingForOfficer = state.caseWork.status === "Pending approval" && role.canApproveEscalation;

  const badge = (item: NavItem): string | null => {
    if (item.screen === "data" && !fixed) return "1";
    if (item.screen === "cases" && (!state.caseWork.claimFixed || pendingForOfficer)) return "1";
    if (item.screen === "risk") return "7";
    return null;
  };

  const health: Array<{ label: string; status: string; tone: Tone }> = [
    { label: "Ingestion", status: fixed ? "Cash feed late" : "1 batch on hold", tone: "warn" },
    { label: "GraphBase", status: state.sim.graph ? "Down, snapshots only" : "Healthy, lag 42 s", tone: state.sim.graph ? "bad" : "ok" },
    { label: "Scoring", status: "Healthy, 18 min ago", tone: "ok" },
    { label: "LLM", status: state.sim.llm ? "Down, drafting paused" : "Healthy, Groq", tone: state.sim.llm ? "bad" : "ok" },
    {
      label: "API",
      status:
        backend === "online" ? (profile ? "Live data" : "Connected, demo sign-in") : backend === "checking" ? "Checking" : backend === "offline" ? (profile ? "Offline" : "Offline, demo data") : "Demo data",
      tone: backend === "online" ? "ok" : backend === "offline" ? (profile ? "bad" : "warn") : "mute",
    },
  ];

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
      <div className="flex flex-col gap-1.5 rounded-xl border border-dashed border-[#34506f] p-2.5">
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
      </div>
      <div className="mt-2.5 flex items-center gap-2.5 border-t border-[#1e3350] px-1.5 py-2">
        <span aria-hidden="true" className="inline-flex size-[34px] items-center justify-center rounded-full bg-sidebar-active text-[13px] font-bold text-brand-light">
          {roleInitials(role)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold text-white">{role.name}</div>
          <div className="text-[11.5px] text-sidebar-muted">{role.label}</div>
        </div>
        <button
          type="button"
          className={cn(SIDE_BUTTON, "w-auto")}
          onClick={() => {
            signOut();
            router.push(ROUTES.signIn);
          }}
        >
          Sign out
        </button>
      </div>
    </aside>
  );
}
