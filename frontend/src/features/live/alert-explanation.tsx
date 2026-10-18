import { SensitiveText } from "@/components/ui/sensitive";

/** Structured detector explanations and legacy text are both supported. Text is shown through the PII filter. */
export function AlertExplanation({ explanation }: { explanation: string | { summary?: string; reasons?: string[] } | null }) {
  const summary = typeof explanation === "string" ? explanation : explanation?.summary;
  const reasons = typeof explanation === "object" && explanation ? explanation.reasons : undefined;
  return <>
    {typeof summary === "string" && summary ? <p className="text-[13.5px]"><SensitiveText value={summary} /></p> : null}
    {Array.isArray(reasons) && reasons.length > 0 ? <ul className="list-disc pl-5 text-[13.5px]">{reasons.filter((reason) => typeof reason === "string").map((reason, i) => <li key={i}><SensitiveText value={reason} /></li>)}</ul> : null}
  </>;
}
