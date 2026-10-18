import type { Metadata } from "next";
import { QualityExceptions } from "@/features/data/quality-exceptions";

export const metadata: Metadata = { title: "Quality exceptions" };

export default function DataQualityPage() {
  return <QualityExceptions />;
}
