import type { Metadata } from "next";
import { QualityExceptions } from "@/features/data/quality-exceptions";
import { LiveExceptions } from "@/features/live/live-data";

export const metadata: Metadata = { title: "Quality exceptions" };

export default function DataQualityPage() {
  return (
    <>
      {/* Live backend findings (renders nothing without a live session), then the scripted sample repairs. */}
      <LiveExceptions />
      <QualityExceptions />
    </>
  );
}
