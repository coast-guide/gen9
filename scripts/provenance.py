# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2==2.13.1", "pypi-attestations==0.0.30"]
# [tool.uv]
# exclude-newer = "7 days"
# ///
"""The locked Python packages' PyPI provenance (PEP 740), checked against the repositories they are
published from (docs/development.md, "Signatures and provenance"; docs/plans/manual-e2e.md, P6-D4b).

uv checks no attestation when it installs. This does, for each package locked for gen9-agent and
gen9-cli that PyPI holds provenance for: its Sigstore bundle against its publisher (a repository and
workflow), and its subject against the file's SHA-256 in the lock, so nothing is downloaded. Each
package's repository is pinned in scripts/provenance.json when first seen. A later run fails when
one names another repository, or a package that had provenance has none: how a taken-over project
would show (OWASP A03).

  uv run scripts/provenance.py            # check
  uv run scripts/provenance.py --update   # pin what is new (read each one first)
"""

import asyncio
import json
import sys
import tomllib
from pathlib import Path

import httpx2
from pydantic import ValidationError
from pypi_attestations import (
    Attestation,
    AttestationError,
    ConversionError,
    Distribution,
    Provenance,
    VerificationError,
)

ROOT = Path(__file__).resolve().parent.parent
LOCKS = (ROOT / "gen9-agent/uv.lock", ROOT / "gen9-cli/uv.lock")
PINS = ROOT / "scripts/provenance.json"
INTEGRITY = "https://pypi.org/integrity/{name}/{version}/{file}/provenance"


def locked() -> dict[tuple[str, str], tuple[str, str]]:
    """(name, version) -> (file, sha256) of one file each, from the locks, registry packages only."""
    files: dict[tuple[str, str], tuple[str, str]] = {}
    for lock in LOCKS:
        for package in tomllib.loads(lock.read_text())["package"]:
            if not package.get("source", {}).get("registry"):
                continue
            artifact = (package.get("wheels") or [package.get("sdist")])[0]
            if artifact:
                name = artifact["url"].rsplit("/", 1)[1]
                files[(package["name"], package["version"])] = (
                    name,
                    artifact["hash"].removeprefix("sha256:"),
                )
    return files


def verify(provenance: dict, file: str, digest: str) -> str:
    """The publisher's repository, once every attestation of its bundle verifies for this file."""
    bundle = Provenance.model_validate(provenance).attestation_bundles[0]
    for attestation in bundle.attestations:
        Attestation.model_validate(attestation.model_dump()).verify(
            bundle.publisher, Distribution(name=file, digest=digest)
        )
    return getattr(bundle.publisher, "repository", None) or bundle.publisher.kind


async def check(
    http: httpx2.AsyncClient,
    limit: asyncio.Semaphore,
    one: asyncio.Lock,
    name: str,
    version: str,
    file: str,
    digest: str,
) -> tuple[str, str | None, str | None]:
    async with limit:
        response = await http.get(
            INTEGRITY.format(name=name, version=version, file=file),
            headers={"Accept": "application/vnd.pypi.integrity.v1+json"},
        )
    if response.status_code == 404:
        return name, None, None
    response.raise_for_status()
    try:
        # sigstore's verification is synchronous and refreshes its trust root (TUF) in a shared
        # cache, which two at once fail to: off the event loop, one at a time
        async with one:
            return (
                name,
                await asyncio.to_thread(verify, response.json(), file, digest),
                None,
            )
    # A bundle that doesn't verify is reported; failing to fetch the trust root fails the run
    except (
        AttestationError,
        ConversionError,
        VerificationError,
        ValidationError,
    ) as error:
        return name, None, f"{type(error).__name__}: {error}"


async def main(update: bool) -> int:
    files = await asyncio.to_thread(locked)
    pins: dict[str, str | None] = (
        json.loads(await asyncio.to_thread(PINS.read_text)) if PINS.exists() else {}
    )
    limit, one = asyncio.Semaphore(8), asyncio.Lock()
    async with httpx2.AsyncClient(timeout=30) as http:
        results = await asyncio.gather(
            *(check(http, limit, one, n, v, f, d) for (n, v), (f, d) in files.items())
        )
    problems, new = [], {}
    for name, repository, error in sorted(results):
        if error:
            problems.append(f"{name}: its provenance doesn't verify ({error[:160]})")
        elif name not in pins:
            new[name] = repository
        elif pins[name] and repository is None:
            problems.append(f"{name}: no provenance now; it came from {pins[name]}")
        elif pins[name] and repository != pins[name]:
            problems.append(f"{name}: published from {repository}, pinned {pins[name]}")
    verified = sum(1 for _, repository, error in results if repository and not error)
    print(
        f"{len(results)} locked packages: {verified} with provenance verified for the locked file"
    )
    for line in problems:
        print(f"  {line}")
    if new:
        print(
            f"Not pinned yet, {len(new)}: "
            + ", ".join(f"{n} ({r or 'no provenance'})" for n, r in sorted(new.items()))
        )
        if update:
            pins.update(new)
            await asyncio.to_thread(
                PINS.write_text, json.dumps(dict(sorted(pins.items())), indent=2) + "\n"
            )
            print(f"Pinned them in {PINS.relative_to(ROOT)}")
    return 1 if problems or (new and not update) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--update" in sys.argv[1:])))
