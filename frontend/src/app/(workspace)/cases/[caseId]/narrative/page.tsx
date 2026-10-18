import type { Metadata } from "next";
import { NarrativeView } from "@/features/cases/narrative-view";
import { requirePrimaryCase } from "../primary-case";

export const metadata: Metadata = { title: "Narrative · CASE-0412" };

export default async function CaseNarrativePage({ params }: PageProps<"/cases/[caseId]/narrative">) {
  await requirePrimaryCase(params);
  return <NarrativeView />;
}
