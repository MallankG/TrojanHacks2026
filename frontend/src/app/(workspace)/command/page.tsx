import type { Metadata } from "next";
import { CommandCenter } from "@/features/command/command-center";

export const metadata: Metadata = { title: "Command center" };

export default function CommandPage() {
  return <CommandCenter />;
}
