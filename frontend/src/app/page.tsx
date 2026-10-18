import type { Metadata } from "next";
import { BRAND_DISCLAIMER, BRAND_HEADLINE, BRAND_LEAD } from "@/components/shell/brand-copy";
import { LogoMark } from "@/components/shell/logo";
import { ButtonLink } from "@/components/ui/button";
import { RedirectIfSignedIn } from "@/features/auth/redirect-if-signed-in";
import { ROUTES } from "@/lib/routes";

export const metadata: Metadata = { title: { absolute: "TraceLayer · Governed financial-crime investigation" } };

const FEATURES = [
  { title: "Governed data in", body: "A data-quality gate with a named owner. Rejected rows are kept as evidence, and every transaction links back to its source row." },
  { title: "Ranked, explained risk", body: "Typology rules, anomaly detection and trained models (XGBoost and a time-respecting graph neural network) fused into one ranked queue." },
  { title: "Money-flow graph", body: "Trace funds forward and back, find cycles, fan-in and fan-out, and see which accounts connect a case." },
  { title: "AI drafts, people decide", body: "Agents draft the case narrative, a verifier checks every claim against the evidence, and a reviewer makes the call on a hash-chained audit trail." },
];

/** One-screen landing page; the workspace itself is behind sign-in (demo accounts are listed there). */
export default function Home() {
  return (
    <main id="main" className="flex min-h-screen flex-col bg-sidebar px-6 py-8 text-white sm:px-12 sm:py-10">
      <RedirectIfSignedIn />
      <header className="flex items-center gap-3">
        <LogoMark size={40} />
        <span className="text-[22px] font-bold">TraceLayer</span>
      </header>

      <section className="mx-auto flex w-full max-w-[1080px] flex-1 flex-col justify-center gap-10 py-10">
        <div className="flex max-w-[680px] flex-col gap-5">
          <h1 className="text-[40px] font-semibold leading-[1.1] tracking-[-0.015em] sm:text-[48px]">
            {BRAND_HEADLINE}
          </h1>
          <p className="text-[17px] leading-relaxed text-hero-text">
            {BRAND_LEAD}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <ButtonLink href={ROUTES.signIn} variant="primary" className="px-6">
              Explore the live demo
            </ButtonLink>
            <span className="text-[13px] text-sidebar-muted">Demo accounts for every role are on the sign-in page.</span>
          </div>
        </div>

        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((f) => (
            <li key={f.title} className="flex flex-col gap-2 rounded-[14px] border border-sidebar-line bg-hero p-5">
              <h2 className="text-[15px] font-semibold">{f.title}</h2>
              <p className="text-[13.5px] leading-relaxed text-sidebar-text">{f.body}</p>
            </li>
          ))}
        </ul>
      </section>

      <footer className="text-[12.5px] text-sidebar-muted">
        {BRAND_DISCLAIMER}
      </footer>
    </main>
  );
}
