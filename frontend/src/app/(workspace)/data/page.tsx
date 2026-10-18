import type { Metadata } from "next";
import { SourcesAndBatches } from "@/features/data/sources-and-batches";

export const metadata: Metadata = { title: "Data operations" };

export default function DataPage() {
  return <SourcesAndBatches />;
}
