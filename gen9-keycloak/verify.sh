#!/usr/bin/env bash
# Verify gen9-keycloak on its own, without any app: health, issuer from host and from a container,
# realm settings (including the configure step's), seeded users/roles, client security settings,
# PKCE and redirect allow-list, password policy (dry run on a throwaway user) and email delivery
# to Mailpit.
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
check "second step: authenticator app, then recovery codes" "auth-otp-form:ALTERNATIVE auth-recovery-authn-code-form:ALTERNATIVE" \
  "$(api "$KC/admin/realms/gen9/authentication/flows/browser/executions" | py 'print(" ".join(e["providerId"] + ":" + e["requirement"] for e in sorted(d, key=lambda e: e["priority"]) if e.get("providerId") in ("auth-otp-form", "auth-recovery-authn-code-form")))')"
check "forgot password uses Gen9's flow" "gen9-reset-credentials" "$(printf '%s' "$REALM" | py 'print(d["resetCredentialsFlow"])')"
check "forgot password asks for the second step before a new password" \
  "0:reset-credentials-choose-user:REQUIRED 0:reset-credential-email:REQUIRED 0:gen9-reset-second-step:CONDITIONAL 1:conditional-user-configured:REQUIRED 1:auth-otp-form:ALTERNATIVE 1:auth-recovery-authn-code-form:ALTERNATIVE 0:reset-password:REQUIRED" \
  "$(api "$KC/admin/realms/gen9/authentication/flows/gen9-reset-credentials/executions" | py 'print(" ".join("%s:%s:%s" % (e["level"], e.get("providerId") or e["displayName"], e["requirement"]) for e in d))')"
check "Temporal's web UI: sign in, then admins only (gen9-temporal-ui)" \
  "0:gen9-temporal-ui-sign-in:REQUIRED 1:auth-cookie:ALTERNATIVE 1:gen9-temporal-ui-forms:ALTERNATIVE 2:auth-username-password-form:REQUIRED 2:gen9-temporal-ui-second-step:CONDITIONAL 3:conditional-user-configured:REQUIRED 3:conditional-credential:REQUIRED 3:auth-otp-form:ALTERNATIVE 3:auth-recovery-authn-code-form:ALTERNATIVE 0:gen9-temporal-ui-not-admin:CONDITIONAL 1:conditional-user-role:REQUIRED 1:deny-access-authenticator:REQUIRED" \
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
check "PKCE enforced (no code_challenge)" "Missing parameter: code_challenge_method" \
  "$(curl -s -o /dev/null -w '%{redirect_url}' "$KC/realms/gen9/protocol/openid-connect/auth?client_id=gen9-ui&response_type=code&scope=openid&redirect_uri=$GEN9_UI_URL/auth/callback&state=x" |
    python3 -c 'import sys,urllib.parse as u; print(u.parse_qs(u.urlparse(sys.stdin.read()).query)["error_description"][0])')"
check "unregistered redirect_uri rejected" "1" \
  "$(curl -s "$KC/realms/gen9/protocol/openid-connect/auth?client_id=gen9-ui&response_type=code&scope=openid&redirect_uri=https://evil.example/cb&code_challenge=$(printf 'x%.0s' $(seq 43))&code_challenge_method=S256" | grep -c 'Invalid parameter: redirect_uri' | sed 's/^[1-9][0-9]*$/1/' || true)"

# Password policy + email, on a throwaway user that is deleted afterwards
TMP_EMAIL="verify-$(date +%s)@gen9.test"
api -X POST "$KC/admin/realms/gen9/users" -d "{\"username\":\"$TMP_EMAIL\",\"email\":\"$TMP_EMAIL\",\"enabled\":true,\"firstName\":\"Verify\",\"lastName\":\"Script\"}"
TMP_ID=$(api "$KC/admin/realms/gen9/users?email=$TMP_EMAIL&exact=true" | py 'print(d[0]["id"])')
trap 'api -X DELETE "$KC/admin/realms/gen9/users/$TMP_ID" || true' EXIT
pw_error() { curl -s -X PUT -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  "$KC/admin/realms/gen9/users/$TMP_ID/reset-password" -d "{\"type\":\"password\",\"value\":\"$1\",\"temporary\":false}" |
  python3 -c 'import sys,json; t=sys.stdin.read(); print(json.loads(t)["error"] if t else "accepted")'; }
check "password < 15 chars rejected" invalidPasswordMinLengthMessage "$(pw_error 'fourteen-chars')"
check "blocklisted password rejected" invalidPasswordBlacklistedMessage "$(pw_error 'passwordpassword')"
check "long random password accepted" accepted "$(pw_error "gen9-$(openssl rand -hex 12)")"
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
