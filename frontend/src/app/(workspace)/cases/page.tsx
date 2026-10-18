import { redirect } from "next/navigation";
import { ROUTES } from "@/lib/routes";
import { PRIMARY_CASE } from "@/lib/seed/governance";

export default function CasesIndexPage() {
  redirect(ROUTES.case(PRIMARY_CASE));
}
