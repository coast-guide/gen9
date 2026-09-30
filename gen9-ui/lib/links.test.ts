import { describe, expect, it } from "vitest";

import { cleanLink, realSite } from "@/lib/links";

describe("cleanLink", () => {
  it("drops tracking parameters and keeps the rest", () => {
    expect(cleanLink("https://valkey.io/?utm_source=openai")).toBe("https://valkey.io/");
    expect(cleanLink("https://valkey.io/?trk=public_post_reshare-text")).toBe("https://valkey.io/");
    expect(cleanLink("https://x.test/a?id=7&utm_medium=x&UTM_CAMPAIGN=y#top")).toBe("https://x.test/a?id=7#top");
  });

  it("leaves other links alone", () => {
    expect(cleanLink("https://x.test/a?q=utm")).toBe("https://x.test/a?q=utm");
    expect(cleanLink("mailto:a@x.test?utm_source=x")).toBe("mailto:a@x.test?utm_source=x");
    expect(cleanLink("/chat/1")).toBe("/chat/1");
    expect(cleanLink(undefined)).toBeUndefined();
  });
});

describe("realSite", () => {
  it("says where a link goes when its text names another site (P5-C9)", () => {
    expect(realSite("bbc.co.uk", "https://bbc-news.example/story")).toBe("bbc-news.example");
    expect(realSite("https://www.gov.uk/tax", "https://gov-uk.example/tax")).toBe("gov-uk.example");
  });

  it("stays quiet when the text names the site, or isn't a site", () => {
    expect(realSite("bbc.co.uk", "https://www.bbc.co.uk/news")).toBeNull();
    expect(realSite("docs.python.org", "https://docs.python.org/3/")).toBeNull();
    expect(realSite("python.org", "https://docs.python.org/3/")).toBeNull();
    expect(realSite("the release notes", "https://example.com/notes")).toBeNull();
    expect(realSite("bbc.co.uk", "mailto:news@bbc.co.uk")).toBeNull();
  });

  it("names a punycode host, which can imitate another name", () => {
    expect(realSite("the bank", "https://xn--pple-43d.com/login")).toBe("xn--pple-43d.com");
  });
});
