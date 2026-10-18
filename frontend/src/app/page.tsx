import { redirect } from "next/navigation";
import { ROUTES } from "@/lib/routes";

/** The workspace shell sends signed-out users to sign-in, so the root can go straight to the command center. */
export default function Home() {
  redirect(ROUTES.command);
}
