import Link from "next/link";
import { ROUTES } from "@/lib/routes";
import { buttonClasses } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main id="main" className="flex min-h-screen items-center justify-center p-10">
      <div className="flex max-w-md flex-col gap-3 rounded-[14px] border border-line bg-white p-8">
        <div className="eyebrow">Not found</div>
        <h1 className="text-2xl font-semibold">This record is not in your scope</h1>
        <p className="text-[13.5px] text-muted">It may not exist, or your role cannot see it. Nothing outside your case scope was loaded.</p>
        <Link href={ROUTES.command} className={buttonClasses("primary", "md", "self-start")}>
          Back to the command center
        </Link>
      </div>
    </main>
  );
}
