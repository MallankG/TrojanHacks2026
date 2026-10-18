"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { getSession, useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import { DEFAULT_ENTITY, ENTITIES, ENTITY_ORDER } from "@/lib/seed/entities";
import { CardHeader, Panel } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/misc";
import { PageHeader } from "@/components/ui/page-header";
import { LiveAccountSearch } from "@/features/live/live-entity";

/**
 * Entities index. With a live session it lists live accounts (searchable) above the sample ones;
 * without one it goes straight to the default sample profile, as before.
 */
export function EntitiesIndex() {
  const router = useRouter();
  const profile = useLiveProfile();

  useEffect(() => {
    // Read the stored session directly: the hook reports the server snapshot (null) during hydration.
    if (!getSession()?.profile) router.replace(ROUTES.entity(DEFAULT_ENTITY));
  }, [router, profile]);

  if (!profile) return <Skeleton label="Opening the account profile" />;

  return (
    <>
      <PageHeader title="Account and entity profile" description="Pick an account to see its baseline against what happened. Mismatches are context for review, not proof." />
      <LiveAccountSearch autoFocus />
      <Panel>
        <CardHeader eyebrow="Sample" title="Sample accounts in CASE-0412" description="Seeded demo profiles. They are not backend data." />
        <nav aria-label="Sample accounts" className="flex flex-wrap gap-1.5">
          {ENTITY_ORDER.map((key) => (
            <Link key={key} href={ROUTES.entity(ENTITIES[key].id)} className="inline-flex min-h-9 items-center rounded-full border border-line-strong bg-white px-3 font-mono text-[13px] font-semibold text-ink-2 hover:border-[#8a97ab]">
              {ENTITIES[key].id}
            </Link>
          ))}
        </nav>
      </Panel>
    </>
  );
}
