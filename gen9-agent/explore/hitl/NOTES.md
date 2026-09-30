# Human in the loop: what the probe showed

`probe.py`: deepagents 0.7.18, langgraph 1.2.12, langchain 1.4.2, a Postgres 17 checkpointer
(`AsyncPostgresSaver`) in a throwaway container, and a scripted model, so every run is the same.
It streams exactly as gen9-agent's executor does (`stream_mode=["messages", "updates"]`,
`subgraphs=True`, `version="v2"`).

| Scenario | What happened |
| --- | --- |
| A question: a tool that calls `interrupt()` in the main agent | An `updates` part with `__interrupt__` at `ns=()` carries an `Interrupt` with a 32-hex `id` and the value. `aget_state(config, subgraphs=True).interrupts` lists it. `Command(resume={id: value})` resumes it, and the tool returns the answer. |
| An approval: `interrupt_on={"send_note": {"allowed_decisions": ["approve", "reject"]}}` | The value is the `HITLRequest` (`action_requests`, `review_configs`), raised in `HumanInTheLoopMiddleware.after_model`. The stream was cut right after the approved tool ran, then resumed with the same `Command`: the tool ran once, not twice (its write was kept as a pending write), and the run finished with 2 model calls. |
| A question inside a subagent (`task`) | The interrupt streams twice with the same `id`: in the subagent's namespace (`tools:<task id>`) and again at `ns=()`. Resuming it from the top by that `id` finishes the subagent and then the main agent. |
| Two questions in one model message | Two interrupts with different ids, both pending. Answering only one runs the answered tool and pauses again on the other (same id). Answering both, one after the other, gives both tool results. |
| A new message on a thread whose question was never answered | `PatchToolCallsMiddleware` answers the dangling call with a ToolMessage ("did not complete - no result was recorded"). The new message runs normally, and nothing is left pending. |

What Gen9 takes from it:
- Read interrupts from the top level only (`ns=()`): a subagent's appear there too, with the same
  id.
- The pending interrupts after a turn are `aget_state(...).interrupts`, and the latest checkpoint's
  `metadata.run_id` says which run raised them. An old run's unanswered question never looks like
  the new run's.
- Resume once, when every pending input has its answer: `Command(resume={id: answer, ...})`.
  Resuming a subset works, but it would give a person a second wait.
- A retried resume (the worker died mid-way) passes the same `Command` again. Finished steps are
  kept and not repeated. A tool cut off mid-way runs again, as for any retried turn.
- A run that stops waiting (Stop, or no answer before the timeout) needs no clean-up: the next
  message continues the conversation.
- LangChain's `respond` decision is for "ask user" tools. Deep Agents Code's own `ask_user`
  (`libs/code/deepagents_code/ask_user.py`, MIT) calls `interrupt()` from the tool with
  `{"type": "ask_user", "questions": [...]}`. Its questions are `text`, `multiple_choice` or
  `multi_select`, with an "Other" answer always possible, and it resumes with
  `{"status": "answered", "answers": [...]}`. Gen9's tool follows the same shape.

## A Signal or an Update, while no worker runs

`messages_probe.py`: a Temporal dev server (CLI 1.9.1, Server 1.32.0) and temporalio 1.33.0. A
workflow waits for two messages, and its worker is stopped before they are sent.

- **Signal:** returned in 0.00 s.
- **Update:** `execute_update` and `start_update(wait_for_stage=ACCEPTED)` both raised
  `WorkflowUpdateRPCTimeoutOrCancelledError` after the 5 s `rpc_timeout`.
- **When the worker came back:** it processed the signal and both timed-out Updates, and the
  workflow completed with all three.
- **An Update ID repeated after the workflow completed:** `RPCError: workflow execution already
  completed`.

So a timed-out Update is not a failed one: it may still land later. An API that rolls back the
answer it stored when the Update times out can leave the workflow believing in an answer Postgres
doesn't have. Temporal's own Approval pattern uses a Signal: it is recorded at once, with or
without a worker.

Gen9 therefore does it in this order:
1. store the answer in Postgres, where the first answer wins;
2. commit;
3. send a Signal that carries the id.

The workflow runs the next turn once it has a Signal for every request it waits on. Answering
something already answered sends the Signal again, so an answer whose Signal was lost (Temporal
unreachable at that moment) still reaches the run.

## Stop just as a turn pauses

`stop_race_probe.py`: the real `RunWorkflow` with the unit tests' fake Activities. Each run sends
the cancel the moment the fake turn returns `waiting`, so the cancel races the turn's completion.

- **Temporal dev server** (CLI 1.9.1, Server 1.32.0): 40 of 40 runs ended `cancelled`, each with
  `finish_run(cancelled)` recorded.
- **Time-skipping test server** (the one the unit tests use): about 1 in 8 hung. The history
  stopped at the workflow task that handles the cancel (`activity_task_scheduled`,
  `workflow_execution_cancel_requested`, `workflow_task_started`) and never went further.

So `tests/test_run_workflow.py` presses Stop only once the workflow reports
`Gen9RunState = waiting`, which is the case it means to test.

## Approvals that depend on the run

`approval_probe.py`: deepagents 0.7.18 and langchain 1.4.2 with a scripted model, in memory. One
compiled agent has `interrupt_on={"write_file": {"allowed_decisions": ["approve", "reject"],
"when": asks_first}}`, where `asks_first` reads the run's context
(`request.runtime.context.permission_mode`).

| Run | What happened |
| --- | --- |
| mode `auto` | The write ran without a pause (`HumanInTheLoopMiddleware.after_model` let it through) |
| mode `ask` | The stream ended at `__interrupt__`. The value is a `HITLRequest`: `action_requests` (name, args, description) and `review_configs` (`allowed_decisions`) |
| `ask`, then `{"type": "reject", "message": …}` | The model got an error ToolMessage, "User rejected the tool call for `write_file` with reason: …", and nothing was written |
| `ask`, then `{"type": "approve"}` | The file was written |

So the permission mode can be chosen per chat and per run, passed in the run's context, without
compiling an agent for each mode.
