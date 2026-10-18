import { notFound } from "next/navigation";
import { PRIMARY_CASE } from "@/lib/seed/governance";

/** Only the working case has editable sections; sample cases are summary-only. */
export async function requirePrimaryCase(params: Promise<{ caseId: string }>): Promise<void> {
  const { caseId } = await params;
  if (caseId !== PRIMARY_CASE) notFound();
}
