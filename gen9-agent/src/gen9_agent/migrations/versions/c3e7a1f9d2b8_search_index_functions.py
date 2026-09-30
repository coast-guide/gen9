"""search_index_ensure and search_index_drop_stale: chat_search's per-model HNSW indexes, built
and dropped for the services by functions the owner defines (docs/plans/harness.md, "The services
stop owning the tables").

The API and worker connect as gen9_agent_app, which owns nothing and may create nothing
(migrate.py grants it rows). Building an index needs the table's ownership and CREATE on its
schema, and CREATE would let the services make functions and triggers too. So these two are
their only DDL: SECURITY DEFINER, as gen9_agent, with a fixed search_path, every input checked
here, and EXECUTE for gen9_agent_app alone. The index name and form are search_index.py's.

Builds aren't CONCURRENTLY, which can't run in a function: the worker builds a model's index
before any row uses it (explore/search/NOTES.md: 0.01 s), so chat_search's writes wait briefly.

Revision ID: c3e7a1f9d2b8
Revises: a4d9e2b7c1f5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e7a1f9d2b8"
down_revision: str | None = "a4d9e2b7c1f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# gen9-postgres creates it (scripts/ensure-roles.sh); migrate.py names it too
SERVICES_ROLE = "gen9_agent_app"

# As search_index.py: _MODEL_NAME, index_name(), vector_type() and the settings' memory pattern
_NAME = "'chat_search_hnsw_' || left(encode(sha256(convert_to({}, 'UTF8')), 'hex'), 12)"
_CHECK_MODEL = """
  if {0} is null or {0} !~ '^[A-Za-z0-9._:/@+-]{{1,128}}$' then
    raise exception 'unexpected embedding model name: %', {0};
  end if;"""

ENSURE = f"""
create function search_index_ensure(model text, dims integer, memory text) returns text
language plpgsql security definer set search_path = pg_catalog, public as $$
declare
  kind text := case when dims <= 2000 then 'vector' else 'halfvec' end;
  name text := {_NAME.format("model")};
  valid boolean;
begin{_CHECK_MODEL.format("model")}
  if dims is null or dims < 1 or dims > 4000 then
    raise exception 'no index for % dimensions', dims;
  end if;
  if memory is null or memory !~ '^[0-9]{{1,6}}(kB|MB|GB)$' then
    raise exception 'unexpected maintenance_work_mem: %', memory;
  end if;
  select i.indisvalid into valid
    from pg_index i join pg_class c on c.oid = i.indexrelid
    where i.indrelid = 'public.chat_search'::regclass and c.relname = name;
  if valid then
    return name;
  end if;
  if valid is false then  -- a CONCURRENTLY build that failed, before this function
    execute format('drop index public.%I', name);
  end if;
  perform set_config('maintenance_work_mem', memory, true);
  execute format(
    'create index %I on public.chat_search using hnsw ((embedding::%s(%s)) %s) where embed_model = %L',
    name, kind, dims, kind || '_cosine_ops', model);
  return name;
end $$
"""

DROP_STALE = f"""
create function search_index_drop_stale(keep text) returns setof text
language plpgsql security definer set search_path = pg_catalog, public as $$
declare
  keep_name text := {_NAME.format("keep")};
  name text;
begin{_CHECK_MODEL.format("keep")}
  -- While any row still has another model's embedding, every index stays
  if exists (select from public.chat_search
             where embedding is not null and embed_model is distinct from keep) then
    return;
  end if;
  for name in
    select c.relname from pg_index i join pg_class c on c.oid = i.indexrelid
    where i.indrelid = 'public.chat_search'::regclass
      and c.relname like 'chat\\_search\\_hnsw\\_%' and c.relname <> keep_name
  loop
    execute format('drop index public.%I', name);
    return next name;
  end loop;
end $$
"""

SIGNATURES = (
    "search_index_ensure(text, integer, text)",
    "search_index_drop_stale(text)",
)


def upgrade() -> None:
    if not op.get_bind().scalar(
        sa.text("select exists (select from pg_roles where rolname = :role)"),
        {"role": SERVICES_ROLE},
    ):
        raise RuntimeError(
            f"role {SERVICES_ROLE} doesn't exist: gen9-postgres creates it on start "
            "(make setup, then make up STACKS=postgres)"
        )
    op.execute(ENSURE)
    op.execute(DROP_STALE)
    for signature in SIGNATURES:
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {SERVICES_ROLE}")


def downgrade() -> None:
    for signature in SIGNATURES:
        op.execute(f"drop function {signature}")
