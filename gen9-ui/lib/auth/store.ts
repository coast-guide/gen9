import "server-only";

import { createClient } from "redis";

import { hashId, seal, unseal } from "@/lib/auth/crypto";
import { env } from "@/lib/env";

function connect() {
  return createClient({ url: env().SESSION_STORE_URL })
    .on("error", (error: Error) => console.error("[session-store]", error.message))
    .connect();
}

// One connection per server process (kept across dev hot reloads)
const globalStore = globalThis as unknown as { gen9SessionStore?: ReturnType<typeof connect> };

function store(): ReturnType<typeof connect> {
  const pending =
    globalStore.gen9SessionStore ??
    connect().catch((error) => {
      globalStore.gen9SessionStore = undefined;
      throw error;
    });
  globalStore.gen9SessionStore = pending;
  return pending;
}

export type SessionRecord = {
  sub: string;
  sid: string | null;
  email: string | null;
  name: string | null;
  givenName: string | null;
  roles: string[];
  accessToken: string;
  accessTokenExpiresAt: number; // epoch ms
  refreshToken: string | null;
  refreshTokenExpiresAt: number; // epoch ms; the session lives as long as it can be refreshed
  idToken: string;
  authTime: number | null; // epoch s
  createdAt: number; // epoch ms
};

const K = {
  session: (hash: string) => `gen9:session:${hash}`,
  bySid: (sid: string) => `gen9:session-by-sid:${sid}`,
  bySub: (sub: string) => `gen9:session-by-sub:${sub}`,
  lock: (hash: string) => `gen9:session-lock:${hash}`,
  txn: (state: string) => `gen9:auth-txn:${state}`,
  jti: (jti: string) => `gen9:logout-jti:${jti}`,
};

function ttlSeconds(record: SessionRecord): number {
  return Math.max(1, Math.floor((record.refreshTokenExpiresAt - Date.now()) / 1000));
}

export async function saveSession(id: string, record: SessionRecord): Promise<void> {
  const client = await store();
  const hash = hashId(id);
  const ttl = ttlSeconds(record);
  // The user index outlives sessions that ended (signed out, logged out by Keycloak, expired) and
  // its TTL restarts with every save: drop those entries here, or it grows for as long as the user
  // keeps signing in
  const indexed = await client.sMembers(K.bySub(record.sub));
  const alive = indexed.length ? await client.mGet(indexed.map(K.session)) : [];
  const ended = indexed.filter((indexedHash, i) => alive[i] === null && indexedHash !== hash);

  const multi = client.multi().set(K.session(hash), seal(record, K.session(hash)), { EX: ttl });
  // Indexes for back-channel logout (by Keycloak session id) and "sign out everywhere" (by user)
  if (record.sid) multi.sAdd(K.bySid(record.sid), hash).expire(K.bySid(record.sid), ttl);
  if (ended.length) multi.sRem(K.bySub(record.sub), ended);
  multi.sAdd(K.bySub(record.sub), hash).expire(K.bySub(record.sub), 2_592_000);
  await multi.exec();
}

export async function readSession(id: string): Promise<SessionRecord | null> {
  const key = K.session(hashId(id));
  const sealed = await (await store()).get(key);
  return sealed ? unseal<SessionRecord>(sealed, key) : null;
}

export async function deleteSession(id: string): Promise<void> {
  await (await store()).del(K.session(hashId(id)));
}

async function deleteHashes(indexKey: string): Promise<number> {
  const client = await store();
  const hashes = await client.sMembers(indexKey);
  if (hashes.length) await client.del(hashes.map(K.session));
  await client.del(indexKey);
  return hashes.length;
}

/** Back-channel logout: drop every app session bound to a Keycloak session (sid). */
export function deleteSessionsBySid(sid: string): Promise<number> {
  return deleteHashes(K.bySid(sid));
}

/** Drop every app session of a user (sub). */
export function deleteSessionsBySub(sub: string): Promise<number> {
  return deleteHashes(K.bySub(sub));
}

/** Serialize refreshes of one session: Keycloak rotates refresh tokens, so two concurrent refreshes would race. */
export async function withSessionLock<T>(id: string, fn: () => Promise<T>): Promise<T | undefined> {
  const client = await store();
  const lockKey = K.lock(hashId(id));
  const acquired = await client.set(lockKey, "1", { NX: true, PX: 10_000 });
  if (!acquired) return undefined;
  try {
    return await fn();
  } finally {
    await client.del(lockKey);
  }
}

export type AuthTransaction = { codeVerifier: string; nonce: string; returnTo: string; action?: string; removing?: string; createdAt: number };

export async function saveTransaction(state: string, txn: AuthTransaction): Promise<void> {
  await (await store()).set(K.txn(state), seal(txn, K.txn(state)), { EX: 600 });
}

/** One-time read: the transaction is deleted as it is read, so a callback URL cannot be replayed. */
export async function takeTransaction(state: string): Promise<AuthTransaction | null> {
  const sealed = await (await store()).getDel(K.txn(state));
  return sealed ? unseal<AuthTransaction>(sealed, K.txn(state)) : null;
}

/** Remember a logout token id until it expires; false if it was already used (replay). */
export async function rememberLogoutTokenId(jti: string, ttlSeconds: number): Promise<boolean> {
  const set = await (await store()).set(K.jti(jti), "1", { NX: true, EX: Math.max(60, ttlSeconds) });
  return set === "OK";
}

export async function pingStore(): Promise<boolean> {
  return (await (await store()).ping()) === "PONG";
}
