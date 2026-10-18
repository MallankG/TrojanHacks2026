import type { Metadata } from "next";
import { CaseChecklist } from "@/features/cases/case-checklist";
import { requirePrimaryCase } from "../primary-case";

export const metadata: Metadata = { title: "Evidence checklist · CASE-0412" };

export default async function CaseChecklistPage({ params }: PageProps<"/cases/[caseId]/checklist">) {
  await requirePrimaryCase(params);
  return <CaseChecklist />;
}
