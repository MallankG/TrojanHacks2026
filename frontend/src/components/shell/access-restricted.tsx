"use client";

import type { ReactNode } from "react";
import { ROUTES } from "@/lib/routes";
import { useRole } from "@/lib/store/hooks";
import { ButtonLink } from "@/components/ui/button";
import { Panel } from "@/components/ui/card";

/** Row-level security in the UI: case screens render nothing for roles without case scope. */
export function CaseAccessGate({ children }: { children: ReactNode }) {
  const role = useRole();
  if (role.canAccessCases) return <>{children}</>;
  return (
    <Panel className="max-w-[640px]">
      <div className="eyebrow">Access restricted</div>
      <h1 className="text-[26px] font-semibold">Your role has no case access</h1>
      <p className="text-[13.5px] text-muted">You are signed in as {role.label}. Row-level security limits this role to data operations, lineage and audit. No case records were loaded.</p>
      <div className="flex flex-wrap gap-2.5">
        <ButtonLink href={ROUTES.data} variant="primary">
          Go to data operations
        </ButtonLink>
        <ButtonLink href={ROUTES.governance()}>Open lineage and audit</ButtonLink>
      </div>
    </Panel>
  );
}
