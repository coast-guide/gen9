"""Just-in-time provisioning: every authenticated call upserts the caller's row by Keycloak `sub`."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import CurrentPrincipal
from .db_unavailable import cannot_write
from .deps import get_session
from .models import User


async def upsert_user(
    session: AsyncSession, sub: str, email: str | None, name: str | None
) -> User:
    statement = (
        insert(User)
        .values(sub=sub, email=email, name=name)
        .on_conflict_do_update(
            index_elements=[User.sub],
            set_={"email": email, "name": name, "last_seen_at": func.now()},
        )
        .returning(User)
    )
    user = (await session.execute(statement)).scalar_one()
    await session.commit()
    return user


async def get_current_user(
    principal: CurrentPrincipal, session: Annotated[AsyncSession, Depends(get_session)]
) -> User:
    try:
        return await upsert_user(
            session, principal.sub, principal.email, principal.name
        )
    except DBAPIError as e:
        # Nothing can be saved (read-only, a full disk): who is asking is their row as it is, so
        # they can still read; a write of theirs then answers 503 (db_unavailable.py, P6-B3). A
        # person Gen9 doesn't know yet can't be recorded: that 503 now.
        if not cannot_write(e):
            raise
        await session.rollback()
        user = await session.scalar(select(User).where(User.sub == principal.sub))
        if user is None:
            raise
        return user


CurrentUser = Annotated[User, Depends(get_current_user)]
