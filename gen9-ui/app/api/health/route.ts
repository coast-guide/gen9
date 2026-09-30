import { pingStore } from "@/lib/auth/store";

export const dynamic = "force-dynamic";

/** Container healthcheck: the server answers and the session store is reachable. */
export async function GET() {
  try {
    await pingStore();
    return Response.json({ status: "ok" });
  } catch {
    return Response.json({ status: "session store unavailable" }, { status: 503 });
  }
}
