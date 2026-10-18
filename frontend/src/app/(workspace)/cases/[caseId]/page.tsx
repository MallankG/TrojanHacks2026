import type { Metadata } from "next";
import { PRIMARY_CASE } from "@/lib/seed/governance";
import { CaseOverview } from "@/features/cases/case-overview";

export async function generateMetadata({ params }: PageProps<"/cases/[caseId]">): Promise<Metadata> {
  const { caseId } = await params;
  return { title: `${caseId} · Case` };
}

export default async function CaseOverviewPage({ params }: PageProps<"/cases/[caseId]">) {
  const { caseId } = await params;
  return caseId === PRIMARY_CASE ? <CaseOverview /> : null;
}
