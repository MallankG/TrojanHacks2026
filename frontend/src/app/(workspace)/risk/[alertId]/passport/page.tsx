import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ALERT_IDS, isAlertId } from "@/lib/seed/alerts";
import { EvidencePassportView } from "@/features/risk/evidence-passport";

export function generateStaticParams() {
  return ALERT_IDS.map((alertId) => ({ alertId }));
}

export async function generateMetadata({ params }: PageProps<"/risk/[alertId]/passport">): Promise<Metadata> {
  const { alertId } = await params;
  return { title: `Evidence passport · ${alertId}` };
}

export default async function PassportPage({ params }: PageProps<"/risk/[alertId]/passport">) {
  const { alertId } = await params;
  if (!isAlertId(alertId)) notFound();
  return <EvidencePassportView alertId={alertId} />;
}
