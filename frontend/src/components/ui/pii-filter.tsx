import type { ReactNode } from "react";
import { SensitiveText } from "./sensitive";

/**
 * Plain-string content in shared display components passes through the PII filter: emails, SSNs,
 * card numbers and phone numbers are redacted and account references follow the mask toggle.
 * Server-safe (no hooks here); SensitiveText is the client boundary.
 */
export function piiFiltered(node: ReactNode): ReactNode {
  return typeof node === "string" ? <SensitiveText value={node} /> : node;
}
