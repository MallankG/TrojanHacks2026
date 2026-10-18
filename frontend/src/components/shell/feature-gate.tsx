"use client";

import type { ReactNode } from "react";
import { ROUTES } from "@/lib/routes";
import { FEATURE_FLAGS, type FeatureFlagKey } from "@/lib/seed/governance";
import { useDemoState } from "@/lib/store/hooks";
import { ButtonLink } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";

export function useFlag(flag: FeatureFlagKey): boolean {
  return useDemoState().policy.flags[flag];
}

/** Renders children only when the feature flag is on. Flags are configuration, not authorization. */
export function FeatureGate({ flag, children, quiet }: { flag: FeatureFlagKey; children: ReactNode; quiet?: boolean }) {
  const on = useFlag(flag);
  if (on) return <>{children}</>;
  if (quiet) return null;
  const label = FEATURE_FLAGS.find((f) => f.key === flag)?.label ?? flag;
  return (
    <Callout
      tone="mute"
      title={`${label} is turned off.`}
      action={
        <ButtonLink href={ROUTES.governance("settings")} size="sm">
          Open feature flags
        </ButtonLink>
      }
    >
      A BSA officer can turn it on in Governance, Policy and settings.
    </Callout>
  );
}
