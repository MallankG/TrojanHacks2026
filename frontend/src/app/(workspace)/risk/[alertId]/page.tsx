import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ALERT_IDS, isAlertId } from "@/lib/seed/alerts";
import { AlertDetail } from "@/features/risk/alert-detail";
import { AlertQueue } from "@/features/risk/alert-queue";
import { LiveAlertDetail } from "@/features/live/live-alerts";

export function generateStaticParams() {
  return ALERT_IDS.map((alertId) => ({ alertId }));
}

/** Live backend alerts are addressed by their database UUID; seeded alerts keep their AL-#### ids. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata({ params }: PageProps<"/risk/[alertId]">): Promise<Metadata> {
  const { alertId } = await params;
  return { title: `${UUID.test(alertId) ? "Live alert" : alertId} · Risk intelligence` };
}

export default async function AlertPage({ params }: PageProps<"/risk/[alertId]">) {
  const { alertId } = await params;
  if (UUID.test(alertId)) {
    return (
      <div className="flex flex-wrap items-start gap-4">
        <AlertQueue selectedId={alertId} />
        <LiveAlertDetail alertId={alertId} />
      </div>
    );
  }
  if (!isAlertId(alertId)) notFound();
  return (
    <div className="flex flex-wrap items-start gap-4">
      <AlertQueue selectedId={alertId} />
      <AlertDetail alertId={alertId} />
    </div>
  );
}
