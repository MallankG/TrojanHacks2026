import type { Metadata } from "next";
import { ImpactRadar } from "@/features/data/impact-radar";

export const metadata: Metadata = { title: "Impact radar" };

export default function DataImpactPage() {
  return <ImpactRadar />;
}
