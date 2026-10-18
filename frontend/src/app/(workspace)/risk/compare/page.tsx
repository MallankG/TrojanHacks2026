import type { Metadata } from "next";
import { DetectorComparison } from "@/features/risk/detector-comparison";

export const metadata: Metadata = { title: "Compare detectors" };

export default function ComparePage() {
  return <DetectorComparison />;
}
