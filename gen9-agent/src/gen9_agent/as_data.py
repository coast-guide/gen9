"""Text that isn't the person's own words, put into a chat as a message: in a block that says it's
data, not instructions, as the agent's rule for what it reads has it (agents/gen9/AGENTS.md). A
trigger's text (tasks.py), a background task's answer (background.py) and a grader's findings
(outcomes.py) come this way: each can carry what a web page or a file said (gen9-learn.md, M9,
F19). As Claude Code's routines wrap a trigger's text."""


def as_data(tag: str, text: str, note: str) -> str:
    """`text` in a `<tag>` block the text can't close itself, then `note` saying what it is."""
    inert = text.replace(f"</{tag}>", f"<\\/{tag}>")
    return f"<{tag}>\n{inert}\n</{tag}>\n{note}"
