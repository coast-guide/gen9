// Reads Renovate's JSON log (LOG_FORMAT=json, LOG_LEVEL=debug) and prints what its lookups found
// for the stacks' images: pins behind their own tag first, then newer releases. Exits 1 if Renovate
// logged an error, found no images or couldn't look one up, so a failed lookup isn't read as "all current".
import { readFileSync } from "node:fs";

const behind = [];
const newer = [];
let images = 0;
let errors = [];
const unchecked = [];
for (const line of readFileSync(process.argv[2], "utf8").split("\n")) {
  let entry;
  try {
    entry = JSON.parse(line);
  } catch {
    continue;
  }
  if (entry.level >= 50) errors.push(entry.msg);
  if (entry.msg !== "packageFiles with updates") continue;
  for (const files of Object.values(entry.config)) {
    for (const file of files) {
      for (const dep of file.deps ?? []) {
        if (dep.skipReason) continue;
        images++;
        const image = `${dep.depName}:${dep.currentValue}`;
        // A lookup that failed (a registry's rate limit, say) leaves no updates: not "current"
        for (const warning of dep.warnings ?? []) unchecked.push(`  ${file.packageFile}  ${image}: ${warning.message}`);
        const pinned = (dep.currentDigest ?? "").slice(7, 19);
        for (const update of dep.updates ?? []) {
          const digest = (update.newDigest ?? "").slice(7, 19);
          if (update.updateType === "digest") behind.push(`  ${file.packageFile}  ${image}  ${pinned} → ${digest}`);
          else newer.push(`  ${update.updateType.padEnd(5)}  ${file.packageFile}  ${image} → ${update.newValue}`);
        }
      }
    }
  }
}

if (errors.length || !images) {
  console.error(`Renovate's lookup failed${errors.length ? `: ${[...new Set(errors)].join("; ")}` : " (no images found)"}`);
  process.exit(1);
}
// An image named twice in one file (gen9-models' Postgres) is one row
const rebuilt = [...new Set(behind)].sort();
const releases = [...new Set(newer)].sort();
console.log(`${images} image references checked.`);
console.log(rebuilt.length ? `\nRebuilt under the same tag since pinned (often base-image fixes), ${rebuilt.length}:` : "\nEvery pin matches its tag.");
for (const row of rebuilt) console.log(row);
if (releases.length) {
  console.log(`\nNewer releases, ${releases.length} (read their notes first; a major one may need a migration):`);
  for (const row of releases) console.log(row);
}
if (unchecked.length) {
  console.log(`\nCouldn't check, ${unchecked.length}:`);
  for (const row of [...new Set(unchecked)].sort()) console.log(row);
}
console.log("\nTo move a pin: docker buildx imagetools inspect <image>:<tag>, then its digest in the file.");
process.exit(unchecked.length ? 1 : 0);
