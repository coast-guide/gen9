// A throwaway person, once deleted in Keycloak, removed from Gen9 at once (gen9-agent-sweep --only,
// in the worker), as an admin would. The deleted-users sweep would get there in its own time,
// except that it holds while more than half of the people Gen9 knows are gone from Keycloak, and
// on a small install a few checks' leftovers are that many (docs/plans/manual-e2e.md, P8-B4). One
// Gen9 never saw, or one deleted through Gen9 already, it declines: nothing to do. Each person on
// their own: given several, the sweep deletes nobody if one is declined, which left demotion.mjs's
// signed-in person behind when its target had never signed in (P8-Z5).
import { execFileSync } from "node:child_process";

export function forget(...subs) {
  for (const sub of subs.filter(Boolean)) {
    try {
      execFileSync("docker", ["exec", "gen9-agent-worker-1", "gen9-agent-sweep", "--only", sub], { stdio: "ignore" });
    } catch {
      // declined (unknown to Gen9, or still in Keycloak): nothing left to remove here
    }
  }
}
