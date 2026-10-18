import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ENTITIES, ENTITY_ORDER, entityByAccountId } from "@/lib/seed/entities";
import { EntityProfile } from "@/features/entity/entity-profile";

export function generateStaticParams() {
  return ENTITY_ORDER.map((key) => ({ accountId: ENTITIES[key].id }));
}

export async function generateMetadata({ params }: PageProps<"/entities/[accountId]">): Promise<Metadata> {
  const { accountId } = await params;
  return { title: `${accountId} · Entity profile` };
}

export default async function EntityPage({ params }: PageProps<"/entities/[accountId]">) {
  const { accountId } = await params;
  const entity = entityByAccountId(accountId);
  if (!entity) notFound();
  return <EntityProfile entityKey={entity.key} />;
}
