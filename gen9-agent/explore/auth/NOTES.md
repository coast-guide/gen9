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
