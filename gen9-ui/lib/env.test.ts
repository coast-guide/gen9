import { describe, expect, it } from "vitest";

import { envProblems } from "./env";

// Settings as make setup writes them (the values are placeholders of the right shape)
const valid = {
  APP_URL: "http://localhost:14000",
  KEYCLOAK_ISSUER: "http://localhost:15000/realms/gen9",
  KEYCLOAK_CLIENT_ID: "gen9-ui",
  KEYCLOAK_CLIENT_SECRET: "x",
  SESSION_SECRET: "a".repeat(64),
  SESSION_STORE_URL: "redis://:pw@valkey:6379/0",
  GEN9_AGENT_URL: "http://gen9-agent:8000",
};

describe("envProblems", () => {
  it("finds nothing wrong with settings as make setup writes them", () => {
    expect(envProblems(valid)).toEqual([]);
  });

  it("names each wrong or missing setting and why, for the log at start", () => {
    const problems = envProblems({ ...valid, SESSION_SECRET: "a".repeat(48), GEN9_AGENT_URL: undefined });
    expect(problems).toHaveLength(2);
    expect(problems[0]).toMatch(/^SESSION_SECRET: .*64/);
    expect(problems[1]).toMatch(/^GEN9_AGENT_URL: /);
  });

  it("takes a privacy setting Compose passed empty as unset, and refuses a notice that isn't a URL", () => {
    const empty = { PRIVACY_CONTROLLER: "", PRIVACY_CONTACT: "", PRIVACY_DPO: "", PRIVACY_AUTHORITY: "", PRIVACY_NOTICE_URL: "" };
    expect(envProblems({ ...valid, ...empty })).toEqual([]);
    expect(envProblems({ ...valid, PRIVACY_NOTICE_URL: "our privacy page" })).toEqual([expect.stringMatching(/^PRIVACY_NOTICE_URL: /)]);
  });
});
