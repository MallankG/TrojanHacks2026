import type { ScreenKey } from "@/lib/store/state";

export const ROUTES = {
  signIn: "/sign-in",
  command: "/command",
  data: "/data",
  dataQuality: "/data/quality",
  dataContracts: "/data/contracts",
  risk: "/risk",
  riskAlert: (id: string) => `/risk/${id}`,
  riskCompare: "/risk/compare",
  graph: "/graph",
  entity: (accountId: string) => `/entities/${accountId}`,
  copilot: "/copilot",
  cases: "/cases",
  case: (id: string, tab?: CaseTab) => `/cases/${id}${tab && tab !== "overview" ? `/${tab}` : ""}`,
  governance: (tab?: GovernanceTab) => `/governance${tab && tab !== "lineage" ? `/${tab}` : ""}`,
} as const;

export type CaseTab = "overview" | "checklist" | "narrative" | "disposition" | "report";
export type GovernanceTab = "lineage" | "models" | "drift" | "pipelines" | "audit" | "exports" | "settings";

export interface NavItem {
  screen: ScreenKey;
  label: string;
  href: string;
  /** Path prefix used to highlight the active item. */
  match: string;
  icon: IconName;
  caseScoped?: boolean;
}

export type IconName = "command" | "data" | "risk" | "graph" | "entity" | "copilot" | "cases" | "governance";

export const NAV_GROUPS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Monitor",
    items: [
      { screen: "command", label: "Command center", href: ROUTES.command, match: "/command", icon: "command" },
      { screen: "data", label: "Data operations", href: ROUTES.data, match: "/data", icon: "data" },
    ],
  },
  {
    label: "Investigate",
    items: [
      { screen: "risk", label: "Risk intelligence", href: ROUTES.risk, match: "/risk", icon: "risk", caseScoped: true },
      { screen: "graph", label: "Graph workbench", href: ROUTES.graph, match: "/graph", icon: "graph", caseScoped: true },
      { screen: "entity", label: "Entity profile", href: ROUTES.entity("ACC-2208"), match: "/entities", icon: "entity", caseScoped: true },
      { screen: "copilot", label: "AI copilot", href: ROUTES.copilot, match: "/copilot", icon: "copilot", caseScoped: true },
      { screen: "cases", label: "Cases and reports", href: ROUTES.case("CASE-0412"), match: "/cases", icon: "cases", caseScoped: true },
    ],
  },
  { label: "Govern", items: [{ screen: "governance", label: "Governance and audit", href: ROUTES.governance(), match: "/governance", icon: "governance" }] },
];

export const SCREEN_TITLES: Record<ScreenKey, { eyebrow: string; title: string }> = {
  command: { eyebrow: "Screen 1 · Monitor", title: "Financial crime command center" },
  data: { eyebrow: "Screen 2 · Monitor", title: "Data operations and governance" },
  risk: { eyebrow: "Screen 3 · Investigate", title: "Risk intelligence" },
  graph: { eyebrow: "Screen 4 · Investigate", title: "Graph investigation workbench" },
  entity: { eyebrow: "Screen 5 · Investigate", title: "Account and entity profile" },
  copilot: { eyebrow: "Screen 6 · Investigate", title: "AI investigation copilot" },
  cases: { eyebrow: "Screen 7 · Investigate", title: "Case management and report builder" },
  governance: { eyebrow: "Screen 8 · Govern", title: "Governance, model and audit center" },
};

export function screenForPath(pathname: string): ScreenKey {
  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      if (pathname === item.match || pathname.startsWith(`${item.match}/`)) return item.screen;
    }
  }
  return "command";
}

export const CASE_SCOPED_SCREENS: ScreenKey[] = ["risk", "graph", "entity", "copilot", "cases"];
