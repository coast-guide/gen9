/** Which Gen9 runs: its version, one for all of Gen9, and the commit its images were built from
 * (gen9-agent's `GET /v1/version`; docs/plans/deploy.md, U7). */
export type Version = { version: string; commit: string };

/** "0.1.0, commit 0123abc", or "0.1.0, built from local code" when the images were built here. */
export function versionWords(v: Version): string {
  return v.commit ? `${v.version}, commit ${v.commit.slice(0, 7)}` : `${v.version}, built from local code`;
}
