"""Names clients, workers and workflows must agree on. Plain values only: workflow modules import
this inside Temporal's sandbox."""

# Agent turns: long, model-bound, few at once per worker
AGENT_QUEUE = "gen9-agent"
# Workflow tasks and short Activities (finishing a run, deletions, sweeps)
SYSTEM_QUEUE = "gen9-system"

# Priority keys: lower runs sooner (1-5; Temporal's default is 3). Activities inherit their
# workflow's priority and fairness key.
PRIORITY_CHAT = 1  # someone is waiting in the chat
PRIORITY_BACKGROUND = 3  # scheduled and background runs
PRIORITY_MAINTENANCE = 5  # sweeps

# Activity names
AGENT_TURN = "agent_turn"
FINISH_RUN = "finish_run"
INDEX_RUN = "index_run"
# The Signal a run waiting for the person gets for each answer, once it is stored in Postgres
# (workflows/runs.py, runs/control.py)
ANSWERED_SIGNAL = "answered"
# Parks a run whose turn failed in a way someone can fix, until they Retry (workflows/runs.py)
PARK_RUN = "park_run"
# The failure type of a turn refused because its person is over their model budget: it resets,
# so the run parks for Retry (runs/executor.py)
BUDGET_EXCEEDED = "BudgetExceeded"
# ...and over their requests per minute (model_router.over_rate_limit)
RATE_LIMITED = "RateLimited"

# Search Activities (SYSTEM_QUEUE; workflows/search.py)
CURRENT_EMBED_MODEL = "current_embed_model"
ENSURE_SEARCH_INDEX = "ensure_search_index"
REINDEX_BATCH = "reindex_batch"
DROP_STALE_SEARCH_INDEXES = "drop_stale_search_indexes"


def run_workflow_id(run_id: str) -> str:
    return f"run-{run_id}"


# The connector directory's Activity (SYSTEM_QUEUE; workflows/directory.py)
SYNC_DIRECTORY = "sync_directory"
# A scheduled task's firing (SYSTEM_QUEUE; workflows/tasks.py), and removing a person's tasks'
# Schedules when their account goes
FIRE_TASK = "fire_task"
REMOVE_USER_TASKS = "remove_user_tasks"
# A background task's notice to the chat that started it (background.py)
TELL_CHAT = "tell_chat"


def tell_chat_workflow_id(task_run: str) -> str:
    return f"tell-chat-{task_run}"


# A task's outcome (outcomes.py): grading a run, the next run of a revision, the final notice
GRADE_RUN = "grade_run"
CONTINUE_TASK = "continue_task"
NOTIFY_OUTCOME = "notify_outcome"
# Plugin sources' Activities (SYSTEM_QUEUE; workflows/plugins.py)
SYNC_PLUGIN_SOURCE = "sync_plugin_source"
LIST_PLUGIN_SOURCES = "list_plugin_sources"

# Deletion Activities (runs on SYSTEM_QUEUE)
STOP_THREAD_RUNS = "stop_thread_runs"
ERASE_TRACES = "erase_traces"
DELETE_RUN_HISTORIES = "delete_run_histories"
DELETE_THREAD_DATA = "delete_thread_data"
DISABLE_KEYCLOAK_USER = "disable_keycloak_user"
DELETE_USER_DATA = "delete_user_data"
ERASE_MODEL_USAGE = "erase_model_usage"
REVOKE_CONNECTOR_TOKENS = "revoke_connector_tokens"
DELETE_KEYCLOAK_USER = "delete_keycloak_user"
FIND_DELETED_USERS = "find_deleted_users"

# The Schedule that removes Gen9's data of users deleted in Keycloak directly
SWEEP_SCHEDULE_ID = "sweep-deleted-users"
# The Schedule that re-embeds chats after `embed` changes and backfills older ones
REINDEX_SCHEDULE_ID = "reindex-search"
REINDEX_NOW_WORKFLOW_ID = "reindex-search-now"
# The Schedule that keeps the connector directory's copy of the MCP registry
DIRECTORY_SCHEDULE_ID = "sync-directory"
DIRECTORY_NOW_WORKFLOW_ID = "sync-directory-now"
# The Schedule that syncs every plugin source, daily
PLUGINS_SCHEDULE_ID = "sync-plugin-sources"


# A chat's environment (workflows/environment.py): its Activities (SYSTEM_QUEUE), the Update that
# asks for its sandbox, and the Signal that ends it
CREATE_ENVIRONMENT = "create_environment"
RENEW_ENVIRONMENT = "renew_environment"
CHECK_ENVIRONMENT_SECRETS = "check_environment_secrets"
REMOVE_ENVIRONMENT = "remove_environment"
REMOVE_USER_ENVIRONMENTS = "remove_user_environments"
REMOVE_THREAD_ENVIRONMENT = "remove_thread_environment"
# A person's secrets changed: rewrite their running environments' vaults and rules
REFRESH_ENVIRONMENT_SECRETS = "refresh_environment_secrets"
ACQUIRE_UPDATE = "acquire"
END_SIGNAL = "end"


def environment_workflow_id(thread_id: str) -> str:
    return f"environment-{thread_id}"


def refresh_environments_workflow_id(user_sub: str) -> str:
    return f"refresh-environments-{user_sub}"


def delete_thread_workflow_id(thread_id: str) -> str:
    return f"delete-thread-{thread_id}"


def delete_account_workflow_id(sub: str) -> str:
    return f"delete-account-{sub}"


def plugin_source_workflow_id(source_id: str) -> str:
    return f"sync-plugin-source-{source_id}"


def task_schedule_id(task_id: str) -> str:
    """A recurring task's Schedule, and the workflow a one-off's delayed start runs."""
    return f"task-{task_id}"
