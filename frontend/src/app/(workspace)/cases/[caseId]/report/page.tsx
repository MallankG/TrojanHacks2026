import type { Metadata } from "next";
import { ReportBuilder } from "@/features/cases/report-builder";
import { requirePrimaryCase } from "../primary-case";

export const metadata: Metadata = { title: "Report builder · CASE-0412" };

export default async function CaseReportPage({ params }: PageProps<"/cases/[caseId]/report">) {
  await requirePrimaryCase(params);
  return <ReportBuilder />;
}
