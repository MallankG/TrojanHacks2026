import type { EvidenceItem } from "@/lib/types";
import { Callout } from "@/components/ui/callout";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";

/** Shared rendering of one evidence record (viewer dialog and copilot panel). */
export function EvidenceBody({ item, originalLabel = "Original source fields" }: { item: EvidenceItem; originalLabel?: string }) {
  return (
    <div className="flex flex-col gap-3">
      <KeyValueList items={item.fields} />
      {item.original ? (
        <div>
          <h3 className="mb-1.5 text-[13.5px] font-semibold">{originalLabel}</h3>
          <KeyValueList items={item.original} mono />
        </div>
      ) : null}
      {item.lineage ? (
        <div>
          <h3 className="mb-1.5 text-[13.5px] font-semibold">Lineage</h3>
          <ol className="flex flex-wrap items-center gap-1.5" aria-label="Lineage">
            {item.lineage.map((step, i) => (
              <li key={step} className="flex items-center gap-1.5">
                <Pill tone="mute">{step}</Pill>
                {i < item.lineage!.length - 1 ? (
                  <span aria-hidden="true" className="text-xs text-muted">
                    ›
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {item.quote ? <blockquote className="border-l-[3px] border-select bg-select-soft px-3 py-2 text-[13.5px]">{item.quote}</blockquote> : null}
      {item.note ? <Callout tone="mute">{item.note}</Callout> : null}
    </div>
  );
}
