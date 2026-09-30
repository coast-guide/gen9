/** For routes that read a body before they know who sent it (the CSP's reports, Keycloak's
 * logout tokens): a limit on what's held, whatever the sender declares (manual-e2e.md, P4-D1). */

/**
 * A request body as text, up to `max` bytes, or null past it: read chunk by chunk, so a body sent
 * without a length (chunked) is never held whole before it's refused.
 */
export async function readBounded(body: ReadableStream<Uint8Array> | null, max: number): Promise<string | null> {
  if (!body) return "";
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > max) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  return new TextDecoder().decode(Buffer.concat(chunks));
}
