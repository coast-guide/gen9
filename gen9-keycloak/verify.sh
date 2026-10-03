#!/usr/bin/env bash
# Verify gen9-keycloak on its own, without any app: health, issuer from host and from a container,
# realm settings (including the configure step's), seeded users/roles, client security settings,
# PKCE, grants, scopes and redirect allow-list, password policy (dry run on a throwaway user) and
# email delivery to Mailpit.
# Usage: ./verify.sh            (stack must be up: docker compose up -d --build --wait)
set -euo pipefail
# shellcheck source=/dev/null  # the generated .env, not in the repo
cd "$(dirname "$0")" && set -a && . ./.env && set +a

KC="http://localhost:${KEYCLOAK_PORT:-15000}" MGMT="http://localhost:${KEYCLOAK_MANAGEMENT_PORT:-15001}"
MAIL="http://localhost:${MAILPIT_PORT:-15002}" FAIL=0
py() { python3 -c "import sys,json; d=json.load(sys.stdin); $1"; }
check() { # $1=label $2=expected $3=actual
  if [ "$2" = "$3" ]; then printf 'ok    %s\n' "$1"; else printf 'FAIL  %s: expected %s, got %s\n' "$1" "$2" "$3"; FAIL=1; fi
}

ADMIN_TOKEN=$(curl -fsS -d grant_type=password -d client_id=admin-cli -d username="$KC_BOOTSTRAP_ADMIN_USERNAME" \
  --data-urlencode "password=$KC_BOOTSTRAP_ADMIN_PASSWORD" "$KC/realms/master/protocol/openid-connect/token" | py 'print(d["access_token"])')
api() { curl -fsS -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' "$@"; }

check "health/ready" UP "$(curl -fsS "$MGMT/health/ready" | py 'print(d["status"])')"
ISSUER="$KC_HOSTNAME/realms/gen9"
check "issuer (host)" "$ISSUER" "$(curl -fsS "$KC/realms/gen9/.well-known/openid-configuration" | py 'print(d["issuer"])')"
# As gen9-agent and gen9-ui see it: from a container on the gen9-keycloak network
FROM_CONTAINER=$(docker run --rm --network gen9-keycloak curlimages/curl:8.16.0 -fsS \
  "http://gen9-keycloak:8080/realms/gen9/.well-known/openid-configuration")
check "issuer (container)" "$ISSUER" "$(printf '%s' "$FROM_CONTAINER" | py 'print(d["issuer"])')"
check "token endpoint (container, back-channel)" "http://gen9-keycloak:8080/realms/gen9/protocol/openid-connect/token" \
  "$(printf '%s' "$FROM_CONTAINER" | py 'print(d["token_endpoint"])')"

REALM=$(api "$KC/admin/realms/gen9")
check "registration + email verification" "True True True" "$(printf '%s' "$REALM" | py 'print(d["registrationAllowed"], d["verifyEmail"], d["resetPasswordAllowed"])')"
check "brute force protection" "True 5" "$(printf '%s' "$REALM" | py 'print(d["bruteForceProtected"], d["failureFactor"])')"
check "refresh token rotation" "True 0" "$(printf '%s' "$REALM" | py 'print(d["revokeRefreshToken"], d["refreshTokenMaxReuse"])')"
check "Gen9 login + email themes" "gen9 gen9" "$(printf '%s' "$REALM" | py 'print(d.get("loginTheme"), d.get("emailTheme"))')"
check "passkeys enabled" "True" "$(printf '%s' "$REALM" | py 'print(d.get("webAuthnPolicyPasswordlessPasskeysEnabled"))')"
# The one-shot configure step finishes a few seconds after `up --wait` returns
for _ in $(seq 60); do
  CONFIGURE=$(docker compose ps -a configure --format '{{.State}} {{.ExitCode}}')
  [ "${CONFIGURE%% *}" = exited ] && break
  sleep 1
done
check "configure step finished" "exited 0" "$CONFIGURE"
check "sign-in uses Gen9's flow" "gen9-browser" "$(printf '%s' "$REALM" | py 'print(d["browserFlow"])')"
check "sign-in: second step (authenticator app, then recovery codes), and admins need one" \
  "0:auth-cookie:ALTERNATIVE 0:identity-provider-redirector:ALTERNATIVE 0:gen9-browser-forms:ALTERNATIVE 1:auth-username-password-form:REQUIRED 1:gen9-browser-second-step:CONDITIONAL 2:conditional-user-configured:REQUIRED 2:conditional-credential:REQUIRED 2:auth-otp-form:ALTERNATIVE 2:auth-recovery-authn-code-form:ALTERNATIVE 1:gen9-browser-forms-admins:CONDITIONAL 2:conditional-user-role:REQUIRED 2:conditional-credential:REQUIRED 2:conditional-sub-flow-executed:REQUIRED 2:auth-otp-form:REQUIRED" \
  "$(api "$KC/admin/realms/gen9/authentication/flows/gen9-browser/executions" | py 'print(" ".join("%s:%s:%s" % (e["level"], e.get("providerId") or e["displayName"], e["requirement"]) for e in d))')"
check "forgot password uses Gen9's flow" "gen9-reset-credentials" "$(printf '%s' "$REALM" | py 'print(d["resetCredentialsFlow"])')"
check "forgot password asks for the second step before a new password" \
  "0:reset-credentials-choose-user:REQUIRED 0:reset-credential-email:REQUIRED 0:gen9-reset-second-step:CONDITIONAL 1:conditional-user-configured:REQUIRED 1:auth-otp-form:ALTERNATIVE 1:auth-recovery-authn-code-form:ALTERNATIVE 0:reset-password:REQUIRED" \
  "$(api "$KC/admin/realms/gen9/authentication/flows/gen9-reset-credentials/executions" | py 'print(" ".join("%s:%s:%s" % (e["level"], e.get("providerId") or e["displayName"], e["requirement"]) for e in d))')"
check "Temporal's web UI: sign in (admins with their second step), then admins only (gen9-temporal-ui)" \
  "0:gen9-temporal-ui-sign-in:REQUIRED 1:auth-cookie:ALTERNATIVE 1:gen9-temporal-ui-forms:ALTERNATIVE 2:auth-username-password-form:REQUIRED 2:gen9-temporal-ui-second-step:CONDITIONAL 3:conditional-user-configured:REQUIRED 3:conditional-credential:REQUIRED 3:auth-otp-form:ALTERNATIVE 3:auth-recovery-authn-code-form:ALTERNATIVE 2:gen9-temporal-ui-forms-admins:CONDITIONAL 3:conditional-user-role:REQUIRED 3:conditional-credential:REQUIRED 3:conditional-sub-flow-executed:REQUIRED 3:auth-otp-form:REQUIRED 0:gen9-temporal-ui-not-admin:CONDITIONAL 1:conditional-user-role:REQUIRED 1:deny-access-authenticator:REQUIRED" \
  "$(api "$KC/admin/realms/gen9/authentication/flows/gen9-temporal-ui/executions" | py 'print(" ".join("%s:%s:%s" % (e["level"], e.get("providerId") or e["displayName"], e["requirement"]) for e in d))')"
UI_FLOW_ID=$(api "$KC/admin/realms/gen9/authentication/flows" | py 'print(next((f["id"] for f in d if f["alias"] == "gen9-temporal-ui"), "none"))')
check "temporal-ui signs in with gen9-temporal-ui" "$UI_FLOW_ID" \
  "$(api "$KC/admin/realms/gen9/clients?clientId=temporal-ui" | py 'print(d[0].get("authenticationFlowBindingOverrides", {}).get("browser", "unbound"))')"
LOGIN_PAGE=$(curl -fsS "$KC/realms/gen9/protocol/openid-connect/auth?client_id=gen9-ui&response_type=code&scope=openid&redirect_uri=$GEN9_UI_URL/auth/callback&code_challenge=$(printf 'x%.0s' $(seq 43))&code_challenge_method=S256")
check "sign-in page served by the Gen9 theme" "1" "$(printf '%s' "$LOGIN_PAGE" | grep -c '/login/gen9/dist/' | tr -d ' ' | sed 's/^[1-9][0-9]*$/1/')"

ACCOUNT=$(api "$KC/admin/realms/gen9/clients?clientId=account" | py 'print(d[0]["id"])')
for pair in "$GEN9_SEED_ADMIN_EMAIL:gen9-admin,gen9-user" "$GEN9_SEED_USER_EMAIL:gen9-user"; do
  email=${pair%%:*}
  id=$(api "$KC/admin/realms/gen9/users?email=$email&exact=true" | py 'print(d[0]["id"])')
  check "roles of $email" "${pair#*:}" "$(api "$KC/admin/realms/gen9/users/$id/role-mappings/realm/composite" | py 'print(",".join(sorted(r["name"] for r in d if r["name"].startswith("gen9-"))))')"
  [ "$email" != "$GEN9_SEED_ADMIN_EMAIL" ] ||
    check "$email has an authenticator app (the checks answer admins' second step)" "otp" \
      "$(api "$KC/admin/realms/gen9/users/$id/credentials" | py 'print(",".join(sorted(c["type"] for c in d if c["type"] == "otp")))')"
  # Like self-registered users: see and end their own sessions (Keycloak's account API)
  check "$email manages own account" "manage-account view-profile" \
    "$(api "$KC/admin/realms/gen9/users/$id/role-mappings/clients/$ACCOUNT/composite" | py 'print(" ".join(sorted(r["name"] for r in d if r["name"] in ("manage-account", "view-profile"))))')"
done

check "gen9-cli: public, device flow only, asks for consent" "True True False True" \
  "$(api "$KC/admin/realms/gen9/clients?clientId=gen9-cli" | py 'c=d[0]; print(c["publicClient"], c["attributes"].get("oauth2.device.authorization.grant.enabled") == "true", c["standardFlowEnabled"], c["consentRequired"])')"
UI=$(api "$KC/admin/realms/gen9/clients?clientId=gen9-ui")
check "gen9-ui: confidential, code flow only" "False True False False" \
  "$(printf '%s' "$UI" | py 'c=d[0]; print(c["publicClient"], c["standardFlowEnabled"], c["directAccessGrantsEnabled"], c["implicitFlowEnabled"])')"
check "gen9-ui: PKCE S256" "S256" "$(printf '%s' "$UI" | py 'print(d[0]["attributes"]["pkce.code.challenge.method"])')"
# Keycloak's answers are read into a variable, then parsed: data, never piped from curl into an
# interpreter (which OpenSSF Scorecard counts as running a download)
REDIRECT=$(curl -s -o /dev/null -w '%{redirect_url}' "$KC/realms/gen9/protocol/openid-connect/auth?client_id=gen9-ui&response_type=code&scope=openid&redirect_uri=$GEN9_UI_URL/auth/callback&state=x")
check "PKCE enforced (no code_challenge)" "Missing parameter: code_challenge_method" \
  "$(printf '%s' "$REDIRECT" | python3 -c 'import sys,urllib.parse as u; print(u.parse_qs(u.urlparse(sys.stdin.read()).query)["error_description"][0])')"
check "unregistered redirect_uri rejected" "1" \
  "$(curl -s "$KC/realms/gen9/protocol/openid-connect/auth?client_id=gen9-ui&response_type=code&scope=openid&redirect_uri=https://evil.example/cb&code_challenge=$(printf 'x%.0s' $(seq 43))&code_challenge_method=S256" | grep -c 'Invalid parameter: redirect_uri' | sed 's/^[1-9][0-9]*$/1/' || true)"

# OAuth, as OWASP ASVS 5.0's V10.4 asks (config/configure.sh; e2e/oauth.mjs tries the rest live)
CLIENTS=$(api "$KC/admin/realms/gen9/clients?max=1000")
check "no client allows the password or implicit grant" "none" \
  "$(printf '%s' "$CLIENTS" | py 'print(",".join(c["clientId"] for c in d if c.get("directAccessGrantsEnabled") or c.get("implicitFlowEnabled")) or "none")')"
check "every client of the code flow needs PKCE with S256 but temporal-ui (Temporal's UI sends none)" "temporal-ui" \
  "$(printf '%s' "$CLIENTS" | py 'print(",".join(c["clientId"] for c in d if c.get("standardFlowEnabled") and not c.get("bearerOnly") and not c["clientId"].startswith("http") and c.get("attributes", {}).get("pkce.code.challenge.method") != "S256"))')"
check "every public client needs PKCE, those that register themselves too (policy public-clients)" "client-access-type:public pkce-enforcer" \
  "$(api "$KC/admin/realms/gen9/client-policies/policies" | py 'p = next(p for p in d["policies"] if p["name"] == "public-clients"); print(*(c["condition"] + ":" + ",".join(c["configuration"]["type"]) for c in p["conditions"]))') $(api "$KC/admin/realms/gen9/client-policies/profiles" | py 'print(*(e["executor"] for p in d["profiles"] if p["name"] == "pkce" for e in p["executors"]))')"
check "codes last a minute; offline tokens 30 days at most, however often used" "60 True 2592000" \
  "$(api "$KC/admin/realms/gen9" | py 'print(d["accessCodeLifespan"], d["offlineSessionMaxLifespanEnabled"], d["offlineSessionMaxLifespan"])')"
check "Gen9's clients may ask only for what they use" "gen9-agent: | gen9-cli: | gen9-mcp:gen9-a2a,offline_access | gen9-ui: | temporal-ui:" \
  "$(printf '%s' "$CLIENTS" | py 'print(" | ".join(c["clientId"] + ":" + ",".join(sorted(c.get("optionalClientScopes", []))) for c in sorted(d, key=lambda c: c["clientId"]) if c["clientId"] in ("gen9-ui", "gen9-cli", "gen9-mcp", "temporal-ui", "gen9-agent")))')"
check "a client that registers itself may ask for" "gen9-a2a,gen9-mcp,offline_access" \
  "$(api "$KC/admin/realms/gen9/default-optional-client-scopes" | py 'print(",".join(sorted(s["name"] for s in d)))')"
check "registering a client without a token is refused" 403 \
  "$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{"redirect_uris":["https://evil.example/cb"]}' "$KC/realms/gen9/clients-registrations/openid-connect")"

# Password policy + email, on a throwaway user that is deleted afterwards
TMP_EMAIL="verify-$(date +%s)@gen9.test"
api -X POST "$KC/admin/realms/gen9/users" -d "{\"username\":\"$TMP_EMAIL\",\"email\":\"$TMP_EMAIL\",\"enabled\":true,\"firstName\":\"Verify\",\"lastName\":\"Script\"}"
TMP_ID=$(api "$KC/admin/realms/gen9/users?email=$TMP_EMAIL&exact=true" | py 'print(d[0]["id"])')
trap 'api -X DELETE "$KC/admin/realms/gen9/users/$TMP_ID" || true' EXIT
pw_error() {
  answer=$(curl -s -X PUT -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
    "$KC/admin/realms/gen9/users/$TMP_ID/reset-password" -d "{\"type\":\"password\",\"value\":\"$1\",\"temporary\":false}")
  printf '%s' "$answer" | python3 -c 'import sys,json; t=sys.stdin.read(); print(json.loads(t)["error"] if t else "accepted")'
}
check "password < 15 chars rejected" invalidPasswordMinLengthMessage "$(pw_error 'fourteen-chars')"
check "blocklisted password rejected" invalidPasswordBlacklistedMessage "$(pw_error 'passwordpassword')"
TMP_PASSWORD="gen9-$(openssl rand -hex 12)"
check "long random password accepted" accepted "$(pw_error "$TMP_PASSWORD")"
check "a password grant is refused, through Keycloak's admin-cli too" unauthorized_client \
  "$(curl -s -d grant_type=password -d client_id=admin-cli -d "username=$TMP_EMAIL" --data-urlencode "password=$TMP_PASSWORD" \
    "$KC/realms/gen9/protocol/openid-connect/token" | py 'print(d.get("error"))')"
# "Choose a password" states the rules before a first try (theme's gen9PasswordHint): its length must be the realm's
check "the password page's hint says the realm's minimum length" \
  "$(printf '%s' "$REALM" | py 'import re; print(re.search(r"length\((\d+)\)", d["passwordPolicy"]).group(1))')" \
  "$(sed -n 's/.*gen9PasswordHint: "At least \([0-9]*\) characters.*/\1/p' theme/src/login/i18n.ts)"
api -X PUT "$KC/admin/realms/gen9/users/$TMP_ID/execute-actions-email?lifespan=300" -d '["UPDATE_PASSWORD"]'
sleep 2
check "email delivered to Mailpit" 1 "$(curl -fsS -u "gen9:$MAILPIT_UI_PASSWORD" "$MAIL/api/v1/search?query=to:$TMP_EMAIL" | py 'print(d["messages_count"])')"
check "Mailpit's API refuses a caller without its password" 401 "$(curl -s -o /dev/null -w '%{http_code}' "$MAIL/api/v1/messages")"
check "email uses the Gen9 email theme" "Update your Gen9 account" "$(curl -fsS -u "gen9:$MAILPIT_UI_PASSWORD" "$MAIL/api/v1/search?query=to:$TMP_EMAIL" | py 'print(d["messages"][0]["Subject"])')"

if [ "$FAIL" = 0 ]; then echo "all checks passed"; else echo "some checks FAILED"; exit 1; fi
