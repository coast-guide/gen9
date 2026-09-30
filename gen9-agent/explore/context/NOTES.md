# Context: when Deep Agents summarizes, and what it streams

Probe: `summarize_probe.py` (deepagents 0.7.18), the router's `chat` alias (GPT-6 Luna) with
`profile={"max_input_tokens": 12000}`, four user messages of about 4,000 tokens each, streamed
with `stream_mode=["messages", "updates"], version="v2"`.

```
turn 0: messages in state 2; summarization event: False; stream sources {('model', '-'): 7}
turn 1: messages in state 4; summarization event: True (cutoff 2); stream sources {('model', 'summarization'): 88, ('model', '-'): 6}
turn 2: messages in state 6; summarization event: True (cutoff 4); …('model', 'summarization'): 125…
turn 3: messages in state 8; summarization event: True (cutoff 6); …('model', 'summarization'): 120…
recall of turn 0: heron-0
```

- A profile's `max_input_tokens` sets the trigger (85%) and what's kept (10%). Without one (Gen9's
  `chat` had `profile: None`), Deep Agents falls back to 170,000 tokens and six messages.
- The summarizer runs inside the `model` node, and its tokens stream as `messages` parts whose
  metadata has `lc_source: "summarization"`. A consumer that turns every `model` chunk into answer
  text (Gen9's `EventMapper` did) shows the summary as part of the answer.
- State keeps every message. The summary is applied at each model call from the private
  `_summarization_event` (a cutoff index and the summary), so a chat's history isn't rewritten.
- After summarizing, the answer still recalled a detail from before the cutoff.
