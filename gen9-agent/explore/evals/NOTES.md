# Evals: what the probes showed

Langfuse Python SDK 4.15.4 against Gen9's self-hosted Langfuse (v4, events-only),
openevals 0.2.0, the router's `chat` alias.

## Langfuse's experiment runner (`probe.py`)

- `langfuse.run_experiment(name, data, task, evaluators, run_evaluators)` works with an async
  task, a code evaluator, an async openevals judge (`create_async_llm_as_judge` on the router's
  `chat` model) and a run evaluator. The printed summary had item averages (`contains: 0.500`,
  `judge: 0.500`) and the run evaluation (`pass_rate: 0.500`).
- On local data (a list of dicts), `dataset_run_id` and `dataset_run_url` are `None`, and the
  run evaluation is **not stored**: `/api/public/v3/scores` listed the four item scores
  (`contains`, `judge`) and no `pass_rate`. The SDK saves run-level evaluations only with a
  dataset run (`_run_experiment_async`: `if dataset_run_id: self.create_score(dataset_run_id=…)`).
  So Gen9's evals sync their tasks to a Langfuse dataset and run on its items, whose runs keep
  pass rates.
- Passing `DatasetItem` objects as `data` (a filtered list, not the whole dataset) still links a
  dataset run: an item with `id` and `dataset_id` gets a dataset run item, and the run URL comes
  from the first item's `dataset_id`. Confirmed by the harness's canary run: it printed the
  dataset run's URL, and the v3 scores API held `pass rate`, `pass@2`, `pass^2` and `median
  seconds` on the subject `{"kind": "experiment", "id": <the run's id>}`, and the item's scores
  (`passed`, `says`, …) on the subject `observation` of the item's trace.
- `run_experiment` is sync. With an event loop already running it starts a thread with a new loop
  (`run_async_safely`) and blocks on it. Async code calls it through `asyncio.to_thread`, and the
  task's HTTP clients are created inside the task, in the runner's loop.
- v4 events-only mode: `/api/public/scores`, `/api/public/v2/scores` and `/api/public/metrics`
  answer 404 ("not available on deployments running in Langfuse v4 events_only mode");
  `/api/public/v3/scores` answers, with `name`, `value`, `source` and `dataType` by default, and
  what a score belongs to with `fields=core,subject,details`. The dataset-run endpoints
  (`/api/public/datasets/{name}/runs`, `/api/public/dataset-run-items`) answer 404 too; the
  dataset and its items answer (`/api/public/v2/datasets/{name}`, `/api/public/dataset-items`).

## Gen9's side

- Runs are traced with the chat as Langfuse's session (`langfuse_session_id`, runs/executor.py),
  so a trial's chat id finds its run traces while the chat exists. Deleting a chat erases them
  (langfuse_erasure.py).
- Keycloak's realm rotates refresh tokens and refuses a reused one (`revokeRefreshToken: true`,
  `refreshTokenMaxReuse: 0`), and access tokens last 300 s: a harness that runs longer refreshes
  one at a time and keeps the newest pair.
