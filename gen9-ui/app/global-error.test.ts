import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import GlobalError from "@/app/global-error";

describe("GlobalError", () => {
  it("is a whole document in Gen9's words, styled on its own, with Try again (P2-J5)", () => {
    const error = Object.assign(new Error("boom"), { digest: "123" });
    const html = renderToStaticMarkup(createElement(GlobalError, { error, retry: () => {} }));
    expect(html).toMatch(/^<html lang="en"><head>/);
    expect(html).toContain("<title>Something went wrong – Gen9</title>");
    expect(html).toContain("<style>");
    expect(html).toContain("<h1>Gen9 couldn’t load this page.</h1>");
    expect(html).toContain(">Try again</button>");
    expect(html).toContain('<a href="/">Go to the home page</a>');
    expect(html).toContain("Error 123");
    expect(html).not.toContain("boom");
  });
});
