"""`gen9-agent-reseal`: seal every secret Gen9 keeps again with the current key of
`GEN9_SECRET_KEYS`, so an old key can be retired (vault.py; docs/plans/harness.md, "Auth across
the harness").

A vault key is rotated in three steps:
1. put a new key first in `GEN9_SECRET_KEYS`, keeping the old, and restart the API and worker;
2. run this: each value sealed with another key is opened and sealed again with the new one,
   bound to the same owner and row as before. `--check` only counts values per key;
3. once `--check` shows none left under the old key, remove it and restart; `--verify` then
   opens every value with the keys left, and shows none failing.

Without step 2, values sealed with an old key stay until people change them, so the old key could
never go.
"""

import argparse
import asyncio
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import create_engine
from .models import Connector, ConnectorSignIn, EnvironmentSecret, User
from .settings import get_settings
from .vault import Vault


@dataclass(frozen=True)
class Sealed:
    """A sealed column, and what its values are bound to (the vault's associated data)."""

    model: Any
    column: str
    # The row's id that its binding names (its own, or its connector's)
    bound_id: str
    # Appended to (owner's sub, id); nothing for a connector's plain token
    purpose: tuple[str, ...]
    # How the row reaches its owner
    owner: Any


COLUMNS = (
    Sealed(Connector, "sealed_token", "id", (), Connector.user_id),
    Sealed(Connector, "sealed_tokens", "id", ("tokens",), Connector.user_id),
    Sealed(Connector, "sealed_client_secret", "id", ("client",), Connector.user_id),
    Sealed(
        EnvironmentSecret,
        "sealed_value",
        "id",
        ("environment",),
        EnvironmentSecret.user_id,
    ),
    Sealed(
        ConnectorSignIn,
        "sealed",
        "connector_id",
        ("sign-in",),
        select(Connector.user_id)
        .where(Connector.id == ConnectorSignIn.connector_id)
        .scalar_subquery(),
    ),
)


def key_of(sealed: str) -> str:
    return sealed.partition(":")[0]


async def count(engine: AsyncEngine) -> dict[str, Counter]:
    """Sealed values per column, by the id of the key that sealed them."""
    found: dict[str, Counter] = {}
    async with engine.connect() as conn:
        for c in COLUMNS:
            values = (await conn.execute(select(getattr(c.model, c.column)))).scalars()
            found[f"{c.model.__tablename__}.{c.column}"] = Counter(
                key_of(v) for v in values if v
            )
    return found


async def reseal(engine: AsyncEngine, vault: Vault) -> dict[str, int]:
    """Seals again every value not under the current key: how many, per column. A value that
    doesn't open (its key is already gone, or its binding doesn't match) is left as it is and
    counted under "unopened"."""
    done: dict[str, int] = {}
    for c in COLUMNS:
        column = getattr(c.model, c.column)
        # Each row by its own primary key (a sign-in's is its state's hash, not an id)
        [key] = inspect(c.model).primary_key
        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    select(key, getattr(c.model, c.bound_id), column, User.sub)
                    .join(User, User.id == c.owner)
                    .where(column.is_not(None))
                    .with_for_update(of=c.model)
                )
            ).all()
            n = 0
            for row_id, bound_id, value, sub in rows:
                if key_of(value) == vault.current_id:
                    continue
                belongs = (sub, str(bound_id), *c.purpose)
                try:
                    secret = vault.open(value, *belongs)
                except ValueError:
                    done["unopened"] = done.get("unopened", 0) + 1
                    continue
                await conn.execute(
                    update(c.model)
                    .where(key == row_id)
                    .values({c.column: vault.seal(secret, *belongs)})
                )
                n += 1
            done[f"{c.model.__tablename__}.{c.column}"] = n
    return done


async def verify(engine: AsyncEngine, vault: Vault) -> dict[str, tuple[int, int]]:
    """Opens every sealed value with the current key ring, without keeping or showing it: how
    many opened and how many didn't, per column. Run it before removing a key, and after."""
    found: dict[str, tuple[int, int]] = {}
    async with engine.connect() as conn:
        for c in COLUMNS:
            column = getattr(c.model, c.column)
            rows = (
                await conn.execute(
                    select(getattr(c.model, c.bound_id), column, User.sub)
                    .join(User, User.id == c.owner)
                    .where(column.is_not(None))
                )
            ).all()
            opened = failed = 0
            for bound_id, value, sub in rows:
                try:
                    vault.open(value, sub, str(bound_id), *c.purpose)
                    opened += 1
                except ValueError:
                    failed += 1
            found[f"{c.model.__tablename__}.{c.column}"] = (opened, failed)
    return found


def _show(found: dict[str, Counter]) -> str:
    return "\n".join(
        f"  {name}: {', '.join(f'{k} {v}' for k, v in sorted(keys.items())) or 'none'}"
        for name, keys in found.items()
    )


async def _main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="gen9-agent-reseal", description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="only count sealed values per key id"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="only open every sealed value with the current keys, and count what opens",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.gen9_secret_keys is None:
        print("No GEN9_SECRET_KEYS: nothing is sealed.", file=sys.stderr)
        return 1
    vault = Vault(settings.gen9_secret_keys.get_secret_value())
    engine = create_engine(settings)
    try:
        before = await count(engine)
        print(
            f"sealed values by key (the current is {vault.current_id}):\n{_show(before)}"
        )
        if args.verify:
            opened = await verify(engine, vault)
            print(
                "opened with the current keys:\n"
                + "\n".join(
                    f"  {k}: {o} opened, {f} not" for k, (o, f) in opened.items()
                )
            )
            return 0 if all(f == 0 for _, f in opened.values()) else 4
        if args.check:
            old = sum(
                n
                for keys in before.values()
                for k, n in keys.items()
                if k != vault.current_id
            )
            return 0 if old == 0 else 3
        done = await reseal(engine, vault)
        print(
            "sealed again with the current key: "
            + ", ".join(f"{k} {v}" for k, v in done.items())
        )
        print(f"now:\n{_show(await count(engine))}")
        return 1 if done.get("unopened") else 0
    finally:
        await engine.dispose()


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))
