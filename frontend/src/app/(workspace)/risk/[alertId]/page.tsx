import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ALERT_IDS, isAlertId } from "@/lib/seed/alerts";
import { AlertDetail } from "@/features/risk/alert-detail";
import { AlertQueue } from "@/features/risk/alert-queue";

export function generateStaticParams() {
  return ALERT_IDS.map((alertId) => ({ alertId }));
}

export async function generateMetadata({ params }: PageProps<"/risk/[alertId]">): Promise<Metadata> {
  const { alertId } = await params;
  return { title: `${alertId} · Risk intelligence` };
}

export default async function AlertPage({ params }: PageProps<"/risk/[alertId]">) {
  const { alertId } = await params;
  if (!isAlertId(alertId)) notFound();
  return (
    <div className="flex flex-wrap items-start gap-4">
      <AlertQueue selectedId={alertId} />
      <AlertDetail alertId={alertId} />
    </div>
  );
}
