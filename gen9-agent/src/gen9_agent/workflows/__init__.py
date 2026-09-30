"""Temporal Workflow definitions. Only deterministic code lives here: Temporal's sandbox re-imports
these modules to run them, so they import nothing with side effects and do no I/O (the Activities
do). Rules: docs/temporal.md."""
