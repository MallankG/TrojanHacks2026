import type { Metadata } from "next";
import { BRAND_DISCLAIMER, BRAND_HEADLINE, BRAND_LEAD } from "@/components/shell/brand-copy";
import { LogoMark } from "@/components/shell/logo";
import { SignInForm } from "@/features/auth/sign-in-form";

export const metadata: Metadata = { title: "Sign in" };

export default function SignInPage() {
  return (
    <div className="flex min-h-screen flex-wrap bg-canvas">
      <section className="flex min-h-[420px] flex-[1_1_460px] flex-col justify-between gap-8 bg-sidebar p-10 text-white sm:p-14">
        <div className="flex items-center gap-3">
          <LogoMark size={48} />
          <span className="text-[26px] font-bold">TraceLayer</span>
        </div>
        <div className="flex max-w-[520px] flex-col gap-4">
          <h1 className="text-[38px] font-semibold leading-[1.15] tracking-[-0.015em]">{BRAND_HEADLINE}</h1>
          <p className="text-base leading-relaxed text-hero-text">
            {BRAND_LEAD}
          </p>
          <ul className="flex flex-col gap-2.5 text-[15px]">
            <li>Data-quality gate with a named owner</li>
            <li>Row-level lineage from source file to decision</li>
            <li>AI drafts, a verifier checks, a person decides</li>
          </ul>
        </div>
        <p className="text-[12.5px] text-sidebar-muted">{BRAND_DISCLAIMER}</p>
      </section>
      <section className="flex flex-[1_1_460px] items-center justify-center p-10">
        <SignInForm />
      </section>
    </div>
  );
}
