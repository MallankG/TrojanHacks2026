import type { Metadata } from "next";
import { ContractsView } from "@/features/data/contracts-view";

export const metadata: Metadata = { title: "Contracts and catalog" };

export default function DataContractsPage() {
  return <ContractsView />;
}
