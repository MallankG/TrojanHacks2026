import { redirect } from "next/navigation";
import { ROUTES } from "@/lib/routes";
import { DEFAULT_ENTITY } from "@/lib/seed/entities";

export default function EntitiesIndexPage() {
  redirect(ROUTES.entity(DEFAULT_ENTITY));
}
