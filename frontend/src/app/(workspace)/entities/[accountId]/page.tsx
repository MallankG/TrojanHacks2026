import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ENTITIES, ENTITY_ORDER, entityByAccountId } from "@/lib/seed/entities";
import { EntityProfile } from "@/features/entity/entity-profile";
import { LiveEntityProfile } from "@/features/live/live-entity";

export function generateStaticParams() {
  return ENTITY_ORDER.map((key) => ({ accountId: ENTITIES[key].id }));
}

/** Live backend accounts are addressed by their database UUID; seeded accounts keep their ACC-#### ids. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata({ params }: PageProps<"/entities/[accountId]">): Promise<Metadata> {
  const { accountId } = await params;
  return { title: `${UUID.test(accountId) ? "Live account" : accountId} · Entity profile` };
}

export default async function EntityPage({ params }: PageProps<"/entities/[accountId]">) {
  const { accountId } = await params;
  if (UUID.test(accountId)) return <LiveEntityProfile accountId={accountId} />;
  const entity = entityByAccountId(accountId);
  if (!entity) notFound();
  return <EntityProfile entityKey={entity.key} />;
}
