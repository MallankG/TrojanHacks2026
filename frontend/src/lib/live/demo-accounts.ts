import type { BackendRole } from "./session";

/** Public demo accounts on the synthetic `demo-bank` tenant (also in ACCOUNTS_AND_ROLES.md). One shared password. */
export const DEMO_PASSWORD = "TraceLayer2026!Demo";

export const DEMO_ACCOUNTS: ReadonlyArray<{ role: BackendRole; email: string }> = [
  { role: "admin", email: "admin@demo.test" },
  { role: "investigator", email: "investigator@demo.test" },
  { role: "analyst", email: "analyst@demo.test" },
  { role: "reviewer", email: "reviewer@demo.test" },
  { role: "data_steward", email: "steward@demo.test" },
];

export const DEMO_ANALYST_EMAIL = "analyst@demo.test";
