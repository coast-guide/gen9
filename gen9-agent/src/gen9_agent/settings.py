"""Service configuration, read from the environment (see README for the env files)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class DatabaseSettings(BaseSettings):
    """The database alone: all `gen9-agent-migrate` needs, so its container gets no other secret
    (compose.yaml; docs/plans/harness.md, "Auth across the harness")."""

    model_config = SettingsConfigDict(extra="ignore")

    # gen9-postgres (written by gen9-postgres/init-env.sh --agent-env-file)
    database_host: str = "localhost"
    database_port: int = 16000
    database_name: str = "gen9_agent"
    database_user: str = "gen9_agent"
    database_password: SecretStr

    @computed_field
    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.database_user,
            password=self.database_password.get_secret_value(),
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )

    @property
    def database_conninfo(self) -> str:
        """libpq connection string, for psycopg connections outside SQLAlchemy."""
        return self.database_url.set(drivername="postgresql").render_as_string(
            hide_password=False
        )


class Settings(DatabaseSettings):
    # gen9-keycloak (written by gen9-keycloak/init-env.sh --agent-env-file)
    keycloak_issuer: str = Field(
        description="Exact `iss` of access tokens, e.g. http://localhost:15000/realms/gen9"
    )
    # Where *this process* reaches Keycloak when that differs from the issuer's host
    # (containers: http://gen9-keycloak:8080). Keycloak keeps `iss` unchanged.
    keycloak_internal_url: str | None = None
    keycloak_audience: str = "gen9-agent"
    # `azp` values allowed to call this API (clients whose tokens carry aud=gen9-agent)
    keycloak_allowed_clients: frozenset[str] = frozenset({"gen9-ui", "gen9-cli"})
    # Service account used for user management (Keycloak Admin REST API)
    keycloak_admin_client_id: str = "gen9-agent"
    keycloak_admin_client_secret: SecretStr | None = None

    # gen9-models, the model router (written by gen9-models/init-env.sh --agent-env-file).
    # Containers reach it at gen9-models:4000 (compose.yaml); on the host, the port it publishes
    gen9_models_url: str = "http://localhost:19000"
    # Its admin API (erasing a user's records); containers use gen9-models-admin:4001
    gen9_models_admin_url: str = "http://localhost:19001"
    gen9_models_key: SecretStr = Field(
        description="make setup STACKS=models writes it to gen9-agent/models.local.env"
    )
    # The agent's model: an alias the router resolves (gen9-models/config.yaml), never a vendor's.
    # Unset: the model its definition names (agents/gen9/AGENTS.md, `model`)
    agent_model: str | None = None
    # Web search: "router", gen9-models' search API (SearXNG by default; any `chat` model, and the
    # default since gen9-models serves `chat` from OpenRouter), or "model", the model's own tool
    # (OpenAI's, which also opens pages; needs `chat` on OpenAI)
    web_search: Literal["model", "router"] = "router"
    # Rerank search results through gen9-models' `rerank` alias (served by its `local` profile, or
    # a hosted reranker in its config.yaml). Off: results keep the search's own order
    search_rerank: bool = False
    # How often to make older chats searchable by meaning: those from before search, and those
    # embedded by an earlier `embed` model (workflows/search.py); 0 turns the Schedule off
    search_reindex_interval_s: int = 900
    # Postgres memory for building a model's vector index; a build that outgrows it is several
    # times slower (explore/search/NOTES.md)
    search_index_memory: str = Field(default="1GB", pattern=r"^[0-9]{1,6}(kB|MB|GB)$")
    # Keys that seal secrets people give Gen9, such as a connector's token (vault.py): "id:base64"
    # pairs of 32-byte keys, the first seals. make setup generates one; unset, tokens can't be kept
    gen9_secret_keys: SecretStr | None = None
    # Connectors (connectors.py) may reach only public https servers, unless this allows private
    # and loopback addresses too (a server on your own network; never on a shared host)
    connectors_allow_private: bool = False
    # Named hosts a connector may reach although they are private, over http too: a test server or
    # a local sign-in server, without opening every private address ("host" or "host:port")
    connectors_allowed_hosts: list[str] = []
    # Clients the operator registered at authorization servers, for connectors that need sign-in
    # (connector_auth.py): {"<issuer>": {"client_id": "…", "client_secret": "…"}}. Else Gen9 uses a
    # Client ID Metadata Document (when gen9_ui_url is https) or registers itself
    connectors_oauth_clients: dict[str, dict[str, str]] = {}
    # The web app's address as the person's browser reaches it: sign-ins return to it
    gen9_ui_url: str = "http://localhost:14000"
    # The MCP registry the connector directory copies (directory.py): the official one, or any with
    # its v0.1 API, such as an organization's own. How often to sync it; 0 turns the sync off
    mcp_registry_url: str = "https://registry.modelcontextprotocol.io"
    mcp_registry_sync_s: int = Field(default=3600, ge=0)
    # Plugin sources (plugin_sources.py): git repositories admins add, fetched over https from
    # public hosts only, unless named here ("host" or "host:port": private, and over http too; a
    # test server). How often they sync, in seconds; 0 turns the Schedule off
    plugin_sources_allowed_hosts: list[str] = []
    plugin_sources_sync_s: int = Field(default=86400, ge=0)
    # Scheduled tasks (tasks.py): how many one person may keep at a time
    tasks_max_per_person: int = Field(default=10, ge=0)
    # What else one person can make Gen9 hold or do without a model (manual-e2e.md, P4-D2):
    # connectors, whose tools are listed (cached briefly) and put in every turn's prompt;
    # environment secrets (GitHub keeps 100 per repository); and the files chats keep, which
    # live in Postgres (250 MB a chat; OpenAI caps a person's uploads at 10 GB)
    connectors_max_per_person: int = Field(default=50, ge=0)
    secrets_max_per_person: int = Field(default=100, ge=0)
    files_max_bytes_per_person: int = Field(default=10 * 1024**3, ge=0)
    # Background tasks a chat's agent may have unfinished at once (background.py)
    background_tasks_per_chat: int = Field(default=4, ge=0)
    # How often a task may fire outside its schedule (its API trigger and Run now), in an hour: per
    # task and per person, as Claude Code's routines (30 and 100)
    tasks_fires_per_hour: int = Field(default=30, ge=1)
    tasks_fires_per_person_hour: int = Field(default=100, ge=1)
    # The agent API's address as callers outside reach it: a task's trigger URL is shown with it
    gen9_api_public_url: str = "http://localhost:17000"
    # The commit this build was made from: Gen9's image build sets GEN9_COMMIT; empty when built
    # here. Shown with the version to whoever is signed in (/v1/version)
    gen9_commit: str = ""
    # The context a chat's model gets, in tokens (explore/context/NOTES.md): its profile's
    # `max_input_tokens`, so Deep Agents summarizes earlier messages at 85% of it and keeps the
    # latest 10%. Deliberately under the models' windows: answers degrade as input grows
    # (Chroma's "Context Rot"), and each call resends it. At least the smallest budget checked to
    # work (e2e/context.mjs's 12,000; 13,000 held a 100-turn chat, manual-e2e.md, P2-H4): Gen9's
    # own instructions and tools take about 9,500 tokens, and at 8,000 no turn fit
    context_budget_tokens: int = Field(default=200_000, ge=12_000)
    # Gen9 as an MCP server (mcp_server.py): how long `ask` waits for an answer before saying
    # the run is still working
    mcp_ask_wait_s: int = Field(default=120, ge=5, le=600)
    # Browser origins that may call `/mcp` besides the API's own, such as a browser-based MCP
    # client's page. A request carrying any other Origin is refused (403), as MCP's transport
    # requires against DNS rebinding; a client outside a browser sends none
    mcp_allowed_origins: list[str] = []
    # Gen9 as an A2A agent (a2a_server.py): how long a blocking SendMessage waits for the task
    a2a_wait_s: int = Field(default=120, ge=5, le=600)
    # Notification emails about background runs (notices.py): an SMTP server as a URL,
    # smtp://[user:password@]host:port (STARTTLS when offered) or smtps://… (TLS); none: no emails.
    # make setup points it at Mailpit in development
    smtp_url: SecretStr | None = None
    # "true" when this Gen9 sends email notices, for a process that doesn't hold SMTP_URL itself:
    # the API only says so in Settings and sends nothing (compose.yaml sets it from SMTP_URL,
    # empty when that's unset)
    email_notices: str = ""
    smtp_from: str = "Gen9 <gen9@gen9.local>"
    # A chat's environment (environments.py): OpenSandbox's server (gen9-sandbox), where each chat
    # runs its commands and keeps its files. Unset, chats have no environment. Containers reach it
    # at gen9-sandbox:8090 (compose.yaml); on the host, the port gen9-sandbox publishes
    sandbox_url: str | None = None
    sandbox_api_key: SecretStr | None = None
    # What an environment runs: its image (pinned), and each one's share of the machine
    sandbox_image: str = "python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
    sandbox_cpu: str = "1"
    sandbox_memory: str = "1Gi"
    # How long an unused environment lives before it's removed (a later command starts a new one)
    sandbox_idle_s: int = Field(default=1800, ge=60)
    # A command's time limit when the agent gives none; it may ask for more, up to environments.py's
    # MAX_COMMAND_S (under the turn's 60 minutes). Claude Code's are 2 minutes, and 10 at most
    sandbox_command_timeout_s: int = Field(default=600, ge=10, le=3000)
    # Hosts an environment may reach (OpenSandbox's egress policy); none by default, as in the
    # leading products: an agent that reads the web can be told to send what it holds elsewhere
    sandbox_egress_allow: list[str] = []
    # How long a run waits for the person when the agent asks something mid-task; then it ends
    # as expired (workflows/runs.py). Applies to runs started after a change
    run_wait_s: int = Field(default=7 * 24 * 3600, ge=60)

    # How often to remove Gen9's data of users deleted in Keycloak directly; 0 turns it off
    deleted_users_sweep_interval_s: int = 900

    # gen9-temporal: its frontend and Gen9's namespace. Containers use gen9-temporal:7233
    # (compose.yaml); on the host, the port gen9-temporal publishes
    temporal_address: str = "localhost:18001"
    temporal_namespace: str = "gen9"
    # Payload encryption keys, "id:base64,id:base64": the first encrypts, all decrypt (codec.py).
    # make setup STACKS=agent generates one into gen9-agent/.env
    temporal_payload_keys: SecretStr = Field(
        description="make setup STACKS=agent writes it to gen9-agent/.env"
    )
    # Temporal's web UI, the one origin allowed to call the codec endpoint from a browser
    temporal_ui_url: str = "http://localhost:18000"

    # Workers (gen9-agent-worker): agent turns one worker executes at once
    worker_concurrency: int = 4

    @field_validator("smtp_url", mode="before")
    @classmethod
    def _unset_when_empty(cls, value: object) -> object:
        """An empty SMTP_URL is no SMTP: the API's is emptied on purpose (compose.yaml)."""
        return None if value == "" else value

    @property
    def keycloak_base_url(self) -> str:
        """Base URL for back-channel calls (JWKS, token, admin API)."""
        issuer_origin = self.keycloak_issuer.split("/realms/", 1)[0]
        return (self.keycloak_internal_url or issuer_origin).rstrip("/")

    @property
    def realm(self) -> str:
        return self.keycloak_issuer.rstrip("/").rsplit("/realms/", 1)[1]

    @property
    def gen9_mcp_url(self) -> str:
        """Gen9's MCP server, as its tokens' audience (gen9-keycloak's GEN9_MCP_URL)."""
        return f"{self.gen9_api_public_url.rstrip('/')}/mcp"

    @property
    def gen9_a2a_url(self) -> str:
        """Gen9 as an A2A agent: its JSON-RPC endpoint, and its tokens' audience (gen9-keycloak's
        GEN9_A2A_URL)."""
        return f"{self.gen9_api_public_url.rstrip('/')}/a2a"

    @property
    def jwks_url(self) -> str:
        return f"{self.keycloak_base_url}/realms/{self.realm}/protocol/openid-connect/certs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
