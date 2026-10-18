import { redirect } from "next/navigation";
import { ROUTES } from "@/lib/routes";
import { DEFAULT_ALERT } from "@/lib/seed/alerts";

export default function RiskIndexPage() {
  redirect(ROUTES.riskAlert(DEFAULT_ALERT));
}
