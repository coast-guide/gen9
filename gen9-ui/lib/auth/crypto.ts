import "server-only";

import { createCipheriv, createDecipheriv, createHash, hkdfSync, randomBytes } from "node:crypto";

import { env } from "@/lib/env";

// Session records are sealed with AES-256-GCM before they reach Valkey, so a dump of the store
// does not expose refresh tokens. Keys are derived from SESSION_SECRET with HKDF.
let key: Buffer | undefined;

function sealKey(): Buffer {
  key ??= Buffer.from(hkdfSync("sha256", Buffer.from(env().SESSION_SECRET, "hex"), "", "gen9-ui session store v1", 32));
  return key;
}

export function seal(value: unknown): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", sealKey(), iv);
  const body = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), body]).toString("base64url");
}

export function unseal<T>(sealed: string): T | null {
  try {
    const raw = Buffer.from(sealed, "base64url");
    const decipher = createDecipheriv("aes-256-gcm", sealKey(), raw.subarray(0, 12));
    decipher.setAuthTag(raw.subarray(12, 28));
    const body = Buffer.concat([decipher.update(raw.subarray(28)), decipher.final()]);
    return JSON.parse(body.toString("utf8")) as T;
  } catch {
    return null; // tampered, or sealed with an old secret
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
