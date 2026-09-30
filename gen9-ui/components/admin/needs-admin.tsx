import { RefreshOnce } from "@/components/admin/refresh-once";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { refreshRoles } from "@/lib/auth/session";

/**
 * An admin page for someone who isn't an admin: their session says so, or the API does. When only
 * the API does (`demoted`: access removed since the session's token was issued), the tokens are
 * refreshed and the page rendered again once, so the sidebar drops its admin links (P3-D7).
 */
export async function NeedsAdmin({ demoted = false }: { demoted?: boolean }) {
  if (demoted) await refreshRoles();
  return (
    <main className="flex flex-1 items-center justify-center px-6">
      <Empty>
        <EmptyHeader>
          <EmptyTitle>
            <h1>You need admin access</h1>
          </EmptyTitle>
          <EmptyDescription>Ask a Gen9 admin to add you to the admins group.</EmptyDescription>
        </EmptyHeader>
      </Empty>
      {demoted && <RefreshOnce />}
    </main>
  );
}
