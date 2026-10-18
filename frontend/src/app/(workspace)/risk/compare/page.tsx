import type { Metadata } from "next";
import { LiveDetectorComparison } from "@/features/live/live-governance";
import { DetectorComparison } from "@/features/risk/detector-comparison";

export const metadata: Metadata = { title: "Compare detectors" };

export default function ComparePage() {
  // Live backend panel (nothing without a live session), then the seeded tab content.
  return (
    <>
      <LiveDetectorComparison />
      <DetectorComparison />
    </>
  );
}
