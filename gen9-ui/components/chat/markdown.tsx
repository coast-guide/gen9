import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

import { cleanLink, realSite } from "@/lib/links";

// Agent answers are Markdown. Raw HTML is not rendered (react-markdown's default), so model
// output can't inject markup; links open in a new tab without access to this page, and without
// tracking parameters (lib/links.ts). An image from another site is shown as a link, never loaded:
// an injected answer could put the person's data in its address, which loading it sends (OWASP
// LLM01; the CSP's img-src blocks it too, and this doesn't lean on that: manual-e2e.md, P3-E2). A line ending is a line break, as in GitHub's comments: a model
// that answers one item per line (found by hand: manual-e2e.md, P2-J1) wrote lines, not a paragraph.
// react-markdown hands each element its syntax-tree node, which isn't for the DOM
function dom<P extends { node?: unknown }>(props: P): Omit<P, "node"> {
  const rest = { ...props };
  delete rest.node;
  return rest;
}

/** The text of rendered children, for comparing a link's words with its address. */
function plain(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(plain).join("");
  if (node && typeof node === "object" && "props" in node) return plain((node.props as { children?: React.ReactNode }).children);
  return "";
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-gen9">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          // Each block takes its direction from its own text (W3C i18n: dir="auto" on text inserted at
          // run time), so a paragraph in Arabic or Hebrew reads right to left, in a mixed answer too
          p: (props) => <p dir="auto" {...dom(props)} />,
          // A list takes its items' direction, which they follow (its bullets on that side): a list
          // item's own dir would hide its text from the list's dir="auto"
          ul: (props) => <ul dir="auto" {...dom(props)} />,
          ol: (props) => <ol dir="auto" {...dom(props)} />,
          h1: (props) => <h1 dir="auto" {...dom(props)} />,
          h2: (props) => <h2 dir="auto" {...dom(props)} />,
          h3: (props) => <h3 dir="auto" {...dom(props)} />,
          h4: (props) => <h4 dir="auto" {...dom(props)} />,
          blockquote: (props) => <blockquote dir="auto" {...dom(props)} />,
          td: (props) => <td dir="auto" {...dom(props)} />,
          th: (props) => <th dir="auto" {...dom(props)} />,
          // A link whose text names another site than it goes to says where it really goes (P5-C9)
          a: ({ href, children }) => {
            const real = realSite(plain(children), href);
            return (
              <>
                <a href={cleanLink(href)} target="_blank" rel="noopener noreferrer nofollow">
                  {children}
                </a>
                {real && <span className="text-xs text-muted-foreground"> (goes to {real})</span>}
              </>
            );
          },
          // Only Gen9's own images load (react-markdown already drops data: and other unsafe
          // addresses, leaving none)
          img: ({ src, alt }) => {
            const address = typeof src === "string" ? src : "";
            if (/^\/(?!\/)/.test(address)) {
              // eslint-disable-next-line @next/next/no-img-element
              return <img src={address} alt={alt ?? ""} />;
            }
            const label = `Image: ${alt || "untitled"}`;
            return address ? (
              <a href={cleanLink(address)} target="_blank" rel="noopener noreferrer nofollow">
                {label}
              </a>
            ) : (
              <span>{label}</span>
            );
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
