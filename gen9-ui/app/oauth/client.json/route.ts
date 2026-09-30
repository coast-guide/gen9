import { env } from "@/lib/env";

export const dynamic = "force-dynamic";

/**
 * Gen9's Client ID Metadata Document (MCP authorization, draft-ietf-oauth-client-id-metadata-
 * document): when Gen9 has a public https address, this URL is its client_id at the authorization
 * servers of connectors that need sign-in, and they fetch it to learn where sign-ins return.
 */
export async function GET() {
  const origin = new URL(env().APP_URL).origin;
  return Response.json(
    {
      client_id: `${origin}/oauth/client.json`,
      client_name: "Gen9",
      client_uri: origin,
      redirect_uris: [`${origin}/settings/connectors/callback`],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
    },
    { headers: { "Cache-Control": "public, max-age=3600" } },
  );
}
