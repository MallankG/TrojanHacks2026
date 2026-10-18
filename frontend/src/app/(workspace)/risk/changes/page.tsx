import type { Metadata } from "next";
import { AlertChanges } from "@/features/risk/alert-changes";

export const metadata: Metadata = { title: "Alert changes" };

export default function AlertChangesPage() {
  return <AlertChanges />;
}
