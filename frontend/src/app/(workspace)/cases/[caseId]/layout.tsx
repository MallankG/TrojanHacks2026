import { notFound } from "next/navigation";
import { CASES, isCaseId } from "@/lib/seed/governance";
import { CaseHeader } from "@/features/cases/case-header";
import { CaseList } from "@/features/cases/case-list";

export function generateStaticParams() {
  return CASES.map((item) => ({ caseId: item.id }));
}

export default async function CaseLayout({ children, params }: LayoutProps<"/cases/[caseId]">) {
  const { caseId } = await params;
  if (!isCaseId(caseId)) notFound();
  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
      <CaseList selectedId={caseId} />
      <div className="flex min-w-0 flex-[3_1_560px] flex-col gap-4">
        <CaseHeader caseId={caseId} />
        {children}
      </div>
    </div>
  );
}
