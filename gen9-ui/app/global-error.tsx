"use client";

// The root layout failed (its providers, say): Next replaces the whole document with this, without the app's
// stylesheet or theme, so it carries its own few styles, in gen9-design's colours for light and dark. Without it
// Next shows its bare default (docs: file-conventions/error, "Global Error"). The error stays in the server's log
// under the digest; `retry` re-renders the page (P2-J5).
const STYLE = `
  :root { color-scheme: light dark; --bg: #F6F7F9; --fg: #0D0F16; --muted: #5B6170; --button: #0D0F16; --on-button: #F6F7F9; }
  @media (prefers-color-scheme: dark) { :root { --bg: #0D0F16; --fg: #EEF0F4; --muted: #9AA0AE; --button: #EEF0F4; --on-button: #0D0F16; } }
  body { margin: 0; min-height: 100dvh; display: grid; place-items: center; background: var(--bg); color: var(--fg);
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 24rem; padding: 1.5rem; }
  h1 { font-size: 1.5rem; line-height: 1.25; margin: 0; }
  p { color: var(--muted); margin: 0.75rem 0 0; }
  .actions { display: grid; gap: 0.625rem; margin-top: 2rem; }
  button, a { font: inherit; font-weight: 500; min-height: 3rem; border-radius: 999px; display: grid; place-items: center; }
  button { border: 0; background: var(--button); color: var(--on-button); cursor: pointer; }
  a { color: var(--fg); text-decoration: none; }
  button:focus-visible, a:focus-visible { outline: 2px solid currentColor; outline-offset: 2px; }
  small { display: block; margin-top: 1.5rem; color: var(--muted); font-size: 0.75rem; }
`;

export default function GlobalError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <html lang="en">
      <head>
        <title>Something went wrong – Gen9</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>{STYLE}</style>
      </head>
      <body>
        <main>
          <h1>Gen9 couldn’t load this page.</h1>
          <p>Part of Gen9 may be restarting. Try again in a minute; nothing you saved is lost.</p>
          <div className="actions">
            <button type="button" onClick={() => retry()}>
              Try again
            </button>
            {/* A plain link: the app's router is part of what failed */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
            <a href="/">Go to the home page</a>
          </div>
          {error.digest && <small>Error {error.digest}</small>}
        </main>
      </body>
    </html>
  );
}
