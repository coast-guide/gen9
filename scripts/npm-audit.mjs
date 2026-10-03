#!/usr/bin/env node
// npm audit, run in an npm project's folder (make audit; CI): fails on a high or critical advisory,
// as `npm audit --audit-level=high` does, except one accepted in scripts/npm-audit.json, which
// names the advisory, its package, the version it is accepted for (so the package's next version
// ends it), the projects it is accepted in, and why. npm audit has no such list of its own, as
// Grype has for images (scripts/sbom/grype.yaml). An accepted advisory the project no longer has
// fails too, so the list holds only what is still there.
//   cd gen9-ui && node ../scripts/npm-audit.mjs
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { relative } from "node:path";
import { promisify } from "node:util";

const root = new URL("..", import.meta.url).pathname;
const project = relative(root, process.cwd());
const { accepted } = JSON.parse(await readFile(new URL("./npm-audit.json", import.meta.url), "utf8"));
const lock = JSON.parse(await readFile("package-lock.json", "utf8"));
// npm audit exits 1 when it finds anything; its report is on stdout either way
const run = await promisify(execFile)("npm", ["audit", "--json"], { maxBuffer: 64 << 20 }).catch((error) => error);
const audit = JSON.parse(run.stdout || "{}");
if (audit.error || !audit.vulnerabilities) {
  console.error(`npm audit gave no report: ${audit.error?.summary ?? run.stderr ?? run.message}`);
  process.exit(1);
}

// Each advisory on the package it is about (the packages that only depend on it name it by name),
// with the versions of it the lock installs
const found = [];
for (const [name, entry] of Object.entries(audit.vulnerabilities)) {
  for (const via of entry.via) {
    if (typeof via === "string") continue;
    const versions = [...new Set(entry.nodes.map((node) => lock.packages[node]?.version))];
    found.push({ id: via.url.split("/").pop(), name, versions, severity: via.severity, title: via.title, url: via.url });
  }
}

let failed = false;
for (const f of found) {
  if (f.severity !== "high" && f.severity !== "critical") continue;
  const ok = accepted.find(
    (a) => a.advisory === f.id && a.package === f.name && a.in.includes(project) && f.versions.every((v) => v === a.version),
  );
  if (ok) {
    console.log(`accepted ${f.id} ${f.name} ${ok.version}: ${ok.reason}`);
  } else {
    failed = true;
    console.log(`${f.severity} ${f.id} ${f.name} ${f.versions.join(", ")}: ${f.title} (${f.url})`);
  }
}
for (const a of accepted) {
  if (a.in.includes(project) && !found.some((f) => f.id === a.advisory && f.name === a.package)) {
    failed = true;
    console.log(`${a.advisory} ${a.package}: accepted for ${project} in scripts/npm-audit.json, but no longer reported here: take it out`);
  }
}
console.log(failed ? `${project}: npm audit failed` : `${project}: no high or critical advisory but those accepted`);
process.exit(failed ? 1 : 0);
