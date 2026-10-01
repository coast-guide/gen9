import "server-only";

import { createCipheriv, createDecipheriv, createHash, hkdfSync, randomBytes } from "node:crypto";

import { env } from "@/lib/env";

// Session records are sealed with AES-256-GCM before they reach Valkey, so a dump of the store
// does not expose refresh tokens. Keys are derived from SESSION_SECRET with HKDF. Each record is
// bound to its key in the store as associated data, so one copied under another session's key
// doesn't open there: no one with write access to Valkey takes over a session by moving a record
// (docs/plans/manual-e2e.md, P7-C2). Records sealed before that bound nothing, and don't open:
// people sign in once more.
let key: Buffer | undefined;

function sealKey(): Buffer {
  key ??= Buffer.from(hkdfSync("sha256", Buffer.from(env().SESSION_SECRET, "hex"), "", "gen9-ui session store v1", 32));
  return key;
}

/** `value` sealed for `boundTo`, its key in the store. */
export function seal(value: unknown, boundTo: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", sealKey(), iv);
  cipher.setAAD(Buffer.from(boundTo, "utf8"));
  const body = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), body]).toString("base64url");
}

/** What `seal` sealed for `boundTo`; null if it was sealed for something else, with another secret, or changed. */
export function unseal<T>(sealed: string, boundTo: string): T | null {
  try {
    const raw = Buffer.from(sealed, "base64url");
    const decipher = createDecipheriv("aes-256-gcm", sealKey(), raw.subarray(0, 12));
    decipher.setAAD(Buffer.from(boundTo, "utf8"));
    decipher.setAuthTag(raw.subarray(12, 28));
    const body = Buffer.concat([decipher.update(raw.subarray(28)), decipher.final()]);
    return JSON.parse(body.toString("utf8")) as T;
  } catch {
    return null; // tampered, sealed for another key, or with an old secret
  }
}

/** 256-bit random, URL-safe identifier (session ids). */
export function randomId(): string {
  return randomBytes(32).toString("base64url");
}

/** Store keys use a hash of the cookie value, never the value itself. */
export function hashId(id: string): string {
  return createHash("sha256").update(id).digest("base64url");
}
