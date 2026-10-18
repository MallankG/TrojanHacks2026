import type { Metadata } from "next";
import { DispositionPanel } from "@/features/cases/disposition-panel";
import { requirePrimaryCase } from "../primary-case";

export const metadata: Metadata = { title: "Disposition · CASE-0412" };

export default async function CaseDispositionPage({ params }: PageProps<"/cases/[caseId]/disposition">) {
  await requirePrimaryCase(params);
  return <DispositionPanel />;
}
