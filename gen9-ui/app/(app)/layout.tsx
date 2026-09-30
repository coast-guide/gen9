import { MobileNav } from "@/components/app-shell/mobile-nav";
import { SidebarContent } from "@/components/app-shell/sidebar";
import { agentJson, type Thread } from "@/lib/agent";
import { getSession } from "@/lib/auth/session";

export default async function AppLayout({ children }: LayoutProps<"/">) {
  // Signed out, the page sends to sign-in and back to itself (each calls requireSession with its
  // address). A layout renders along with the page, so a redirect here would win and lose the
  // address; Next.js's auth guide keeps such checks out of layouts
  const session = await getSession();
  if (!session) return children;
  const threads = await agentJson<Thread[]>(session, "/v1/threads").catch(() => null);
  const user = { name: session.user.name, email: session.user.email, isAdmin: session.isAdmin };
  const sidebar = <SidebarContent user={user} threads={threads} />;

  return (
    <div className="flex min-h-dvh">
      {/* Past the sidebar's chats in one key (WCAG 2.2 SC 2.4.1, technique G1): hidden until focused */}
      <a
        href="#content"
        className="sr-only rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50"
      >
        Skip to content
      </a>
      <aside className="sticky top-0 hidden h-dvh w-72 shrink-0 border-r bg-sidebar lg:block">{sidebar}</aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <MobileNav title="Gen9">{sidebar}</MobileNav>
        <div id="content" tabIndex={-1} className="flex min-h-0 flex-1 flex-col outline-hidden">
          {children}
        </div>
      </div>
    </div>
  );
}
