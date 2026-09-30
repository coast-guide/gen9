# Design

How Gen9 looks and behaves, decided before it's built. Read the relevant page before changing any
screen in gen9-ui (AGENTS.md, "Before any new piece of work").

| Page | What it holds |
| --- | --- |
| [research.md](research.md) | What the leading agent products, AI guidelines and component libraries do, from primary sources |
| [principles.md](principles.md) | Nine principles every screen follows, each with its sources and where it shows in Gen9 |
| [information-architecture.md](information-architecture.md) | What things are called, where they live (sidebar, routes), and the status words a person sees |
| [components.md](components.md) | The components each screen is built from, the rules for adding one, and the agent components to come |
| [screens/](screens/) | One page per screen: layout, behavior, states, accessibility. Built screens say what they do today; planned ones what they'll do |

Foundations (color, type, radius, icons, themes, mobile-first rules, the logo) live in
[gen9-design](../../gen9-design/README.md), the one source of tokens for every Gen9 surface.

## How it stays true

- **Designed, then built.** A new screen gets its page in `screens/` first, reviewed against the
  principles, and is built to it. A change of direction mid-way updates the page and the plan's
  Decision Log.
- **Checked.** Every screen runs through `e2e/a11y.mjs` (axe, WCAG 2.2 AA, phone and desktop,
  light and dark). The flows that matter have an e2e script that clicks them in Chrome by their
  on-screen words, so a page and the product can't drift apart unnoticed.
- **Research is redone.** Products change monthly, so research is read again from primary sources
  when a screen is designed, not reused as if it were timeless. The commit that adds it dates it.
