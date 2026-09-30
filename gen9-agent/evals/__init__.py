"""Gen9's evals (docs/plans/harness.md, "Evals"; gen9-agent/README.md, "Evals").

Tasks run Gen9 as people use it, through its API on the running stacks. Graders read what the
runs produced, each task is repeated to measure how dependable Gen9 is (pass@k, pass^k), and
Langfuse keeps every run as an experiment on a dataset of the tasks.

- `tasks.py`: the suites, as code.
- `graders.py`: code graders, and a model judge for what code can't check.
- `gen9.py`: Gen9's API as the seeded user.
- `run.py`: the dataset, the trials, and the scores.
"""
