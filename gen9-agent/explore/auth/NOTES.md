# A client only admins may sign in to (P2-E3)

`admins_only/setup.sh` and `probe.py` on a throwaway Keycloak from the gen9-keycloak image
(26.7.4, start-dev, port 18099). The Server Administration Guide's "Explicitly deny/allow access
in conditional flows" puts Condition - user role and Deny access in the browser flow's forms, which
runs only at a sign-in: someone with a Keycloak session passes the Cookie step and is let through
(keycloak/keycloak discussion #38350, open). Built instead as a per-client browser flow:

    0 sign-in (sub-flow)            REQUIRED     Cookie ALTERNATIVE | forms ALTERNATIVE (as the realm's)
    0 not-admin (sub-flow)          CONDITIONAL  Condition - user role gen9-admin, negate REQUIRED
                                                 Deny access (message)                 REQUIRED

| Case | Result |
| --- | --- |
| no admin role, fresh sign-in | turned away with the message (401) |
| admin, fresh | signed in (a code) |
| no admin role, signed in to another client first | turned away; still signed in to the other client after |
| admin, signed in to another client first | signed in |

Keycloak sets its cookies Secure even over http on localhost; httpx's jar doesn't send those, so
the probe keeps cookies itself, as a browser does for localhost. Sub-flow aliases with spaces break
kcadm's paths (`Illegal character in path`): hyphens only.

# Admins need a second step (gen9-learn plan, M10)

`admins_second_step/`: a throwaway Keycloak 26.7.4 (project `exp-kc-2fa`, port 25080, Postgres on
tmpfs, 2 CPUs) from the gen9-keycloak image, with Gen9's realm file and `configure.sh`, and
`probe.mjs` driving Chrome through Puppeteer (e2e's). Its `.env` holds throwaway values:
`KC_DB_PASSWORD`, `KC_BOOTSTRAP_ADMIN_PASSWORD`, `GEN9_UI_CLIENT_SECRET`, `GEN9_AGENT_CLIENT_SECRET`,
`GEN9_TEMPORAL_UI_CLIENT_SECRET`, `GEN9_SEED_ADMIN_PASSWORD`, `GEN9_SEED_USER_PASSWORD` (15+
characters) and `GEN9_SEED_ADMIN_OTP_SECRET`. Then `docker compose up -d --wait`, wait for
`configure` to exit 0, and `node probe.mjs`. Sign-ins go through the account console (the realm's
browser flow) or temporal-ui's client.

Keycloak won't change a built-in flow ("It is illegal to add sub-flow to a built in flow"), so
`configure.sh` builds `gen9-browser`: the built-in's steps without Kerberos and organizations, and
after the forms' second step,

    1 gen9-browser-forms-admins (sub-flow)  CONDITIONAL  Condition - user role gen9-admin     REQUIRED
                                                         Condition - credential, not a passkey REQUIRED
                                                         Condition - sub-flow executed:
                                                           gen9-browser-second-step not-executed REQUIRED
                                                         OTP Form                              REQUIRED

| Case | Result |
| --- | --- |
| A. seeded admin (an `otp` credential from `configure.sh`), password | asked for the code; the code from `.env`'s secret signs in |
| H. the same code again, in another browser, same 30 s | refused ("That code didn't work…"): Keycloak keeps used codes by value for 90 s |
| G. seeded admin, `prompt=login`; then `max_age=0` a few seconds later | the password, then a fresh code, both times |
| B. member, password | signed in, nothing more asked |
| C. new admin with no second step, password | "set up an authenticator app"; done, signed in, `otp` stored |
| E. member signed in, then made admin; the same session signs in again | signed in, no second step asked (the session goes on) |
| E. then signed out by the Admin API, password | "set up an authenticator app" |
| D. admin with only a passkey, by passkey | signed in, nothing more asked |
| D2. the same admin types the password instead | "set up an authenticator app" |
| F. temporal-ui's flow, new admin with no second step | "set up an authenticator app" |
| F2. temporal-ui's flow, member | "Temporal is for admins", nothing asked first |
| configure.sh again | idempotent (`in place`); signs out the admins with no second step (2 here), not the ones with an app or a passkey |

The seeded admin's `otp` credential goes in with `PUT /users/{id}` and a `credentials` entry
(`secretData` `{"value": …}`, `credentialData` `{"subType": "totp", "digits": 6, "period": 30,
"algorithm": "HmacSHA1", "counter": 0}`): `createCredentials` keeps the user's other fields, and
the HMAC key is the secret's raw bytes (a code made that way signs in). Condition - sub-flow
executed looks the flow up by alias in the top-level flow the sign-in runs, so each flow names its
own second step.
