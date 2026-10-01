#!/usr/bin/env bash
# Realm settings that the first-boot import can't express, applied on every `docker compose up`
# with Keycloak's admin CLI (kcadm.sh). Every step is idempotent.
set -euo pipefail

kcadm() { /opt/keycloak/bin/kcadm.sh "$@" --config /tmp/kcadm.config; }
kcadm config credentials --server http://keycloak:8080 --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD"

# Admin events expire after 30 days, as login events do (eventsExpiration). Keycloak keeps their
# expiry apart, in the realm attribute adminEventsExpiration; without it they stayed for good, each
# create or update of an account with its email in it, the account's deletion long past
# (docs/plans/manual-e2e.md, P4-E5). The import sets it on first start; this brings older realms in line
ADMIN_EVENTS_EXPIRATION=2592000
admin_events_expiration() {
  local current=""
  # The whole realm: kcadm's --fields attributes shows its attributes empty (26.7.4)
  [[ $(kcadm get realms/gen9) =~ \"adminEventsExpiration\"\ :\ \"([0-9]*)\" ]] && current=${BASH_REMATCH[1]}
  if [[ $current == "$ADMIN_EVENTS_EXPIRATION" ]]; then
    echo "admin events: expire after $current s"
    return
  fi
  kcadm update realms/gen9 -s "attributes.adminEventsExpiration=$ADMIN_EVENTS_EXPIRATION"
  echo "admin events: set to expire after $ADMIN_EVENTS_EXPIRATION s"
}
admin_events_expiration

# A person is told by email when how they sign in changes: a password set, an authenticator app,
# a passkey or recovery codes added or removed (NIST SP 800-63B-4; docs/plans/manual-e2e.md,
# P6-C5). Keycloak's `email` listener, next to the log's; which events it mails is
# KC_SPI_EVENTS_LISTENER__EMAIL__INCLUDE_EVENTS in compose.yaml
email_listener() {
  local config
  config=$(kcadm get events/config -r gen9)
  if [[ $config =~ \"email\" ]]; then
    echo "sign-in changes: emailed to the person"
  else
    kcadm update events/config -r gen9 -s 'eventsListeners=["jboss-logging","email"]'
    echo "sign-in changes: now emailed to the person"
  fi
}
email_listener

# Back-channel logout: Keycloak tells gen9-ui when a session ends, over the gen9-ui network. The
# import sets the URL on first start only, so a realm imported earlier is brought in line here.
backchannel_logout_url() {
  local id client current=""
  id=$(kcadm get clients -r gen9 -q clientId=gen9-ui --fields id --format csv --noquotes)
  client=$(kcadm get "clients/$id" -r gen9)
  [[ $client =~ \"backchannel\.logout\.url\"\ :\ \"([^\"]*)\" ]] && current=${BASH_REMATCH[1]}
  if [[ $current == "$GEN9_UI_BACKCHANNEL_LOGOUT_URL" ]]; then
    echo "gen9-ui back-channel logout: $current"
    return
  fi
  kcadm update "clients/$id" -r gen9 -s "attributes.\"backchannel.logout.url\"=$GEN9_UI_BACKCHANNEL_LOGOUT_URL"
  echo "gen9-ui back-channel logout: set to $GEN9_UI_BACKCHANNEL_LOGOUT_URL"
}
backchannel_logout_url

# Forgot password: a user with an authenticator app or recovery codes must pass that second step
# before choosing a new password. Keycloak's built-in flow replaces the authenticator instead
# ("Reset OTP"), so access to the email alone was enough to take over the account.
#   Choose User -> Send Reset Email -> [if the user has either: authenticator code | recovery code] -> Reset Password
RESET_FLOW=gen9-reset-credentials SECOND_STEP=gen9-reset-second-step
WANTED_SHAPE="0:reset-credentials-choose-user:REQUIRED 0:reset-credential-email:REQUIRED 0:$SECOND_STEP:CONDITIONAL "
WANTED_SHAPE+="1:conditional-user-configured:REQUIRED 1:auth-otp-form:ALTERNATIVE 1:auth-recovery-authn-code-form:ALTERNATIVE "
WANTED_SHAPE+="0:reset-password:REQUIRED "

# The flow's steps on one line (level:provider or subflow:requirement), to compare with WANTED_SHAPE
flow_shape() {
  kcadm get "authentication/flows/$RESET_FLOW/executions" -r gen9 --fields level,providerId,displayName,requirement --format csv --noquotes |
    while IFS=, read -r level provider name requirement; do printf '%s:%s:%s ' "$level" "${provider:-$name}" "$requirement"; done
}

# Set the requirement of $1's top-level step matching $2 (provider, or subflow alias). The update must
# repeat the step's priority: Keycloak copies it from the body, and a missing one (0) moves the step first
set_requirement() {
  local id="" priority="" step provider name prio level
  while IFS=, read -r step provider name prio level; do # the image has no awk
    if [[ ($provider == "$2" || $name == "$2") && $level == 0 ]]; then id=$step priority=$prio; fi
  done < <(kcadm get "authentication/flows/$1/executions" -r gen9 --fields id,providerId,displayName,priority,level --format csv --noquotes)
  kcadm update "authentication/flows/$1/executions" -r gen9 -b "{\"id\":\"$id\",\"requirement\":\"$3\",\"priority\":$priority}"
}
add_step() { # $1=flow $2=provider $3=requirement: appended as the last step
  kcadm create "authentication/flows/$1/executions/execution" -r gen9 -s provider="$2" -i >/dev/null
  set_requirement "$1" "$2" "$3"
}

reset_flow() {
  if [[ $(flow_shape 2>/dev/null) == "$WANTED_SHAPE" ]]; then
    echo "forgot password: $RESET_FLOW in place"
  else
    local id="" flow alias
    while IFS=, read -r flow alias; do
      if [[ $alias == "$RESET_FLOW" ]]; then id=$flow; fi
    done < <(kcadm get authentication/flows -r gen9 --fields id,alias --format csv --noquotes)
    if [ -n "$id" ]; then
      # Out of date: rebuilt from this definition, unbound first as a bound flow can't be deleted
      kcadm update realms/gen9 -s resetCredentialsFlow="reset credentials"
      kcadm delete "authentication/flows/$id" -r gen9
    fi
    kcadm create authentication/flows -r gen9 -s alias="$RESET_FLOW" -s providerId=basic-flow -s topLevel=true -s builtIn=false \
      -s description="Forgot password, asking for the second step (if the user has one) before a new password" -i >/dev/null
    add_step "$RESET_FLOW" reset-credentials-choose-user REQUIRED
    add_step "$RESET_FLOW" reset-credential-email REQUIRED
    kcadm create "authentication/flows/$RESET_FLOW/executions/flow" -r gen9 -s alias="$SECOND_STEP" -s type=basic-flow \
      -s description="Authenticator code or recovery code, if the user has either" -i >/dev/null
    set_requirement "$RESET_FLOW" "$SECOND_STEP" CONDITIONAL
    add_step "$SECOND_STEP" conditional-user-configured REQUIRED
    add_step "$SECOND_STEP" auth-otp-form ALTERNATIVE
    add_step "$SECOND_STEP" auth-recovery-authn-code-form ALTERNATIVE
    add_step "$RESET_FLOW" reset-password REQUIRED
    [[ $(flow_shape) == "$WANTED_SHAPE" ]] || { echo "forgot password: $RESET_FLOW came out as: $(flow_shape)" >&2; exit 1; }
    echo "forgot password: $RESET_FLOW built"
  fi
  if [[ $(kcadm get realms/gen9 --fields resetCredentialsFlow --format csv --noquotes) == "$RESET_FLOW" ]]; then
    echo "forgot password: bound to $RESET_FLOW"
  else
    kcadm update realms/gen9 -s resetCredentialsFlow="$RESET_FLOW"
    echo "forgot password: now uses $RESET_FLOW"
  fi
}
reset_flow

# Temporal (gen9-temporal): who may use it. A bearer-only client `temporal` holds client roles named
# the way Temporal's default JWT claim mapper reads them (<namespace>:<role>): admins get gen9:admin
# and temporal-system:read, gen9-agent's service account gen9:write. A mapper puts exactly those
# roles into a `permissions` claim on tokens of temporal-ui (Temporal's web UI) and gen9-agent
# (docs/temporal.md, Security).
client_id() { kcadm get clients -r gen9 -q clientId="$1" --fields id --format csv --noquotes; }

temporal_access() {
  local temporal ui agent role mappers
  temporal=$(client_id temporal)
  if [[ -z $temporal ]]; then
    temporal=$(kcadm create clients -r gen9 -s clientId=temporal -s name="Temporal roles" \
      -s bearerOnly=true -s enabled=true -i)
    echo "temporal: client created"
  fi
  # temporal-system:read: the UI's cluster-wide reads (list namespaces, cluster info) are checked
  # against the system scope, which the claim mapper names temporal-system
  # (common/authorization/default_jwt_claim_mapper.go); it adds read access everywhere, while
  # writes stay per namespace
  for role in gen9:admin gen9:write temporal-system:read; do
    if ! kcadm get "clients/$temporal/roles" -r gen9 --fields name --format csv --noquotes | grep -qx "$role"; then
      kcadm create "clients/$temporal/roles" -r gen9 -s name="$role"
      echo "temporal: role $role created"
    fi
  done
  # A role named system:read by an earlier version of this script meant nothing to Temporal
  if kcadm get "clients/$temporal/roles" -r gen9 --fields name --format csv --noquotes | grep -qx "system:read"; then
    kcadm delete "clients/$temporal/roles/system:read" -r gen9
    echo "temporal: removed role system:read"
  fi
  kcadm add-roles -r gen9 --gname admins --cclientid temporal --rolename gen9:admin --rolename temporal-system:read
  kcadm add-roles -r gen9 --uusername service-account-gen9-agent --cclientid temporal --rolename gen9:write
  echo "temporal: admins hold gen9:admin and temporal-system:read, gen9-agent holds gen9:write"

  # Temporal's web UI signs in here (authorization code flow, confidential)
  ui=$(client_id temporal-ui)
  if [[ -z $ui ]]; then
    ui=$(kcadm create clients -r gen9 -s clientId=temporal-ui -s name="Temporal web UI" -s enabled=true \
      -s publicClient=false -s standardFlowEnabled=true -s directAccessGrantsEnabled=false -i)
    echo "temporal-ui: client created"
  fi
  kcadm update "clients/$ui" -r gen9 -s secret="$GEN9_TEMPORAL_UI_CLIENT_SECRET" \
    -s "redirectUris=[\"$GEN9_TEMPORAL_UI_URL/auth/sso/callback\"]" -s "webOrigins=[\"$GEN9_TEMPORAL_UI_URL\"]" \
    -s "attributes.\"post.logout.redirect.uris\"=$GEN9_TEMPORAL_UI_URL/*"
  echo "temporal-ui: redirects to $GEN9_TEMPORAL_UI_URL/auth/sso/callback"

  for client in temporal-ui gen9-agent; do
    agent=$(client_id "$client")
    mappers=$(kcadm get "clients/$agent/protocol-mappers/models" -r gen9 --fields name --format csv --noquotes)
    if ! grep -qx "temporal permissions" <<<"$mappers"; then
      kcadm create "clients/$agent/protocol-mappers/models" -r gen9 -f - <<'JSON'
{"name": "temporal permissions", "protocol": "openid-connect",
 "protocolMapper": "oidc-usermodel-client-role-mapper",
 "config": {"usermodel.clientRoleMapping.clientId": "temporal", "claim.name": "permissions",
            "multivalued": "true", "jsonType.label": "String", "access.token.claim": "true",
            "id.token.claim": "false", "userinfo.token.claim": "false", "introspection.token.claim": "true"}}
JSON
      echo "$client: permissions mapper created"
    fi
  done
}
temporal_access

# Temporal's web UI for admins only, refused at Keycloak in Gen9's words: a non-admin otherwise got
# a token without permissions and the UI sent them back to its sign-in page, over and over. A
# browser flow of temporal-ui's own: signing in as the realm's browser flow does (a Keycloak session,
# or the forms with their second step), then Deny access for anyone without gen9-admin. The check
# follows the whole sign-in step, so it also stops someone who already has a Keycloak session:
# the Server Administration Guide's example, in the forms alone, lets them through
# (keycloak/keycloak discussion #38350; gen9-agent/explore/auth/NOTES.md, P2-E3)
UI_FLOW=gen9-temporal-ui
UI_SHAPE="0:$UI_FLOW-sign-in:REQUIRED 1:auth-cookie:ALTERNATIVE 1:$UI_FLOW-forms:ALTERNATIVE "
UI_SHAPE+="2:auth-username-password-form:REQUIRED 2:$UI_FLOW-second-step:CONDITIONAL 3:conditional-user-configured:REQUIRED "
UI_SHAPE+="3:conditional-credential:REQUIRED 3:auth-otp-form:ALTERNATIVE 3:auth-recovery-authn-code-form:ALTERNATIVE "
UI_SHAPE+="2:$UI_FLOW-forms-admins:CONDITIONAL 3:conditional-user-role:REQUIRED 3:conditional-credential:REQUIRED "
UI_SHAPE+="3:conditional-sub-flow-executed:REQUIRED 3:auth-otp-form:REQUIRED "
UI_SHAPE+="0:$UI_FLOW-not-admin:CONDITIONAL 1:conditional-user-role:REQUIRED 1:deny-access-authenticator:REQUIRED "
# A message key: the Gen9 theme says it, and titles its page after it (theme/src/login/i18n.ts)
UI_DENIED=gen9TemporalAdminsOnly

shape_of() { # $1's steps on one line, as flow_shape
  kcadm get "authentication/flows/$1/executions" -r gen9 --fields level,providerId,displayName,requirement --format csv --noquotes |
    while IFS=, read -r level provider name requirement; do printf '%s:%s:%s ' "$level" "${provider:-$name}" "$requirement"; done
}
add_subflow() { # $1=flow $2=alias (no spaces: kcadm paths) $3=requirement $4=description
  kcadm create "authentication/flows/$1/executions/flow" -r gen9 -s alias="$2" -s type=basic-flow -s description="$4" -i >/dev/null
  set_requirement "$1" "$2" "$3"
}
configure_step() { # $1=flow $2=provider $3=config JSON
  local id="" step provider
  while IFS=, read -r step provider; do if [[ $provider == "$2" ]]; then id=$step; fi; done < <(
    kcadm get "authentication/flows/$1/executions" -r gen9 --fields id,providerId --format csv --noquotes)
  kcadm create "authentication/executions/$id/config" -r gen9 -b "$3" >/dev/null
}

# Admins need a second step (docs/auth-architecture.md): a sub-flow after the forms' own second
# step, for someone with gen9-admin who signed in with a password and passed no second step, as
# they have none. It asks for the authenticator app's code, and Keycloak has someone without an
# app set one up before the sign-in ends ("Conditional 2FA sub-flow with OTP default", Keycloak
# 26.7.4's server_admin/topics/authentication/conditions.adoc). A passkey is a second step already.
# $1=the forms sub-flow, $2=its second step's sub-flow
admins_second_step() {
  add_subflow "$1" "$1-admins" CONDITIONAL "Admins need a second step: set one up if they have none"
  add_step "$1-admins" conditional-user-role REQUIRED
  configure_step "$1-admins" conditional-user-role "{\"alias\":\"$1-admins-role\",\"config\":{\"condUserRole\":\"gen9-admin\"}}"
  add_step "$1-admins" conditional-credential REQUIRED
  configure_step "$1-admins" conditional-credential "{\"alias\":\"$1-admins-credential\",\"config\":{\"credentials\":\"webauthn-passwordless\"}}"
  add_step "$1-admins" conditional-sub-flow-executed REQUIRED
  configure_step "$1-admins" conditional-sub-flow-executed \
    "{\"alias\":\"$1-admins-second-step\",\"config\":{\"flow_to_check\":\"$2\",\"check_result\":\"not-executed\"}}"
  add_step "$1-admins" auth-otp-form REQUIRED
}

# The shape leaves out steps' settings: an install from before the refusal's words became a message
# key gets the key here (manual-e2e.md, P3-D6)
deny_message_in_place() {
  local config="" provider step now
  while IFS=, read -r provider step; do if [[ $provider == deny-access-authenticator ]]; then config=$step; fi; done < <(
    kcadm get "authentication/flows/$UI_FLOW/executions" -r gen9 --fields providerId,authenticationConfig --format csv --noquotes)
  [ -n "$config" ] || return 0
  now=$(kcadm get "authentication/config/$config" -r gen9)
  if [[ $now == *"\"denyErrorMessage\" : \"$UI_DENIED\""* ]]; then
    echo "temporal-ui: its refusal says $UI_DENIED"
  else
    kcadm update "authentication/config/$config" -r gen9 -s "config.denyErrorMessage=$UI_DENIED"
    echo "temporal-ui: its refusal now says $UI_DENIED"
  fi
}
temporal_ui_admins_only() {
  local ui flow="" id alias bound client
  ui=$(client_id temporal-ui)
  if [[ $(shape_of "$UI_FLOW" 2>/dev/null) == "$UI_SHAPE" ]]; then
    echo "temporal-ui: $UI_FLOW in place"
    deny_message_in_place
  else
    while IFS=, read -r id alias; do if [[ $alias == "$UI_FLOW" ]]; then flow=$id; fi; done < <(
      kcadm get authentication/flows -r gen9 --fields id,alias --format csv --noquotes)
    if [ -n "$flow" ]; then
      # Out of date: rebuilt, unbound first as a bound flow can't be deleted. An override goes only
      # when its key is sent empty: Keycloak skips an empty map (RepresentationToModel.updateClient)
      kcadm update "clients/$ui" -r gen9 -s 'authenticationFlowBindingOverrides.browser='
      kcadm delete "authentication/flows/$flow" -r gen9
    fi
    kcadm create authentication/flows -r gen9 -s alias="$UI_FLOW" -s providerId=basic-flow -s topLevel=true -s builtIn=false \
      -s description="Temporal's web UI: sign in, then admins only" -i >/dev/null
    add_subflow "$UI_FLOW" "$UI_FLOW-sign-in" REQUIRED "A Keycloak session, or the forms"
    add_step "$UI_FLOW-sign-in" auth-cookie ALTERNATIVE
    add_subflow "$UI_FLOW-sign-in" "$UI_FLOW-forms" ALTERNATIVE "Username and password, then the second step"
    add_step "$UI_FLOW-forms" auth-username-password-form REQUIRED
    add_subflow "$UI_FLOW-forms" "$UI_FLOW-second-step" CONDITIONAL "Authenticator or recovery code, if the user has one"
    add_step "$UI_FLOW-second-step" conditional-user-configured REQUIRED
    add_step "$UI_FLOW-second-step" conditional-credential REQUIRED
    # As the realm's browser flow: no second step after a passkey
    configure_step "$UI_FLOW-second-step" conditional-credential '{"alias":"gen9-temporal-ui-credential","config":{"credentials":"webauthn-passwordless"}}'
    add_step "$UI_FLOW-second-step" auth-otp-form ALTERNATIVE
    add_step "$UI_FLOW-second-step" auth-recovery-authn-code-form ALTERNATIVE
    admins_second_step "$UI_FLOW-forms" "$UI_FLOW-second-step"
    add_subflow "$UI_FLOW" "$UI_FLOW-not-admin" CONDITIONAL "Anyone without gen9-admin is turned away"
    add_step "$UI_FLOW-not-admin" conditional-user-role REQUIRED
    configure_step "$UI_FLOW-not-admin" conditional-user-role '{"alias":"gen9-temporal-ui-role","config":{"condUserRole":"gen9-admin","negate":"true"}}'
    add_step "$UI_FLOW-not-admin" deny-access-authenticator REQUIRED
    configure_step "$UI_FLOW-not-admin" deny-access-authenticator "{\"alias\":\"gen9-temporal-ui-deny\",\"config\":{\"denyErrorMessage\":\"$UI_DENIED\"}}"
    [[ $(shape_of "$UI_FLOW") == "$UI_SHAPE" ]] || { echo "temporal-ui: $UI_FLOW came out as: $(shape_of "$UI_FLOW")" >&2; exit 1; }
    echo "temporal-ui: $UI_FLOW built"
  fi
  while IFS=, read -r id alias; do if [[ $alias == "$UI_FLOW" ]]; then flow=$id; fi; done < <(
    kcadm get authentication/flows -r gen9 --fields id,alias --format csv --noquotes)
  bound=""
  client=$(kcadm get "clients/$ui" -r gen9)
  [[ $client =~ \"browser\"\ :\ \"([^\"]*)\" ]] && bound=${BASH_REMATCH[1]}
  if [[ $bound == "$flow" ]]; then
    echo "temporal-ui: signs in with $UI_FLOW"
  else
    kcadm update "clients/$ui" -r gen9 -s "authenticationFlowBindingOverrides.browser=$flow"
    echo "temporal-ui: now signs in with $UI_FLOW"
  fi
}
temporal_ui_admins_only

# The realm's browser flow, Gen9's own: Keycloak's built-in one takes no new step ("It is illegal to
# add sub-flow to a built in flow"), and admins need one. Otherwise the built-in's steps (26.7.4),
# without Kerberos and organizations, which Gen9 doesn't use:
#   a Keycloak session | an identity provider's redirect | the forms: username and password (or a
#   passkey) -> [if the user has one, not after a passkey: authenticator code | recovery code]
#   -> [an admin with none: set up an authenticator app]
# Recovery codes are offered next to the authenticator app, so a lost phone doesn't lock the user
# out; the built-in flow ships them disabled
BROWSER_FLOW=gen9-browser
BROWSER_SHAPE="0:auth-cookie:ALTERNATIVE 0:identity-provider-redirector:ALTERNATIVE 0:$BROWSER_FLOW-forms:ALTERNATIVE "
BROWSER_SHAPE+="1:auth-username-password-form:REQUIRED 1:$BROWSER_FLOW-second-step:CONDITIONAL 2:conditional-user-configured:REQUIRED "
BROWSER_SHAPE+="2:conditional-credential:REQUIRED 2:auth-otp-form:ALTERNATIVE 2:auth-recovery-authn-code-form:ALTERNATIVE "
BROWSER_SHAPE+="1:$BROWSER_FLOW-forms-admins:CONDITIONAL 2:conditional-user-role:REQUIRED 2:conditional-credential:REQUIRED "
BROWSER_SHAPE+="2:conditional-sub-flow-executed:REQUIRED 2:auth-otp-form:REQUIRED "
browser_flow() {
  local flow="" id alias
  if [[ $(shape_of "$BROWSER_FLOW" 2>/dev/null) == "$BROWSER_SHAPE" ]]; then
    echo "sign-in: $BROWSER_FLOW in place"
  else
    while IFS=, read -r id alias; do if [[ $alias == "$BROWSER_FLOW" ]]; then flow=$id; fi; done < <(
      kcadm get authentication/flows -r gen9 --fields id,alias --format csv --noquotes)
    if [ -n "$flow" ]; then
      # Out of date: rebuilt, unbound first as a bound flow can't be deleted
      kcadm update realms/gen9 -s browserFlow=browser
      kcadm delete "authentication/flows/$flow" -r gen9
    fi
    kcadm create authentication/flows -r gen9 -s alias="$BROWSER_FLOW" -s providerId=basic-flow -s topLevel=true -s builtIn=false \
      -s description="Sign in: a Keycloak session, or the forms and their second step, which admins must have" -i >/dev/null
    add_step "$BROWSER_FLOW" auth-cookie ALTERNATIVE
    add_step "$BROWSER_FLOW" identity-provider-redirector ALTERNATIVE
    add_subflow "$BROWSER_FLOW" "$BROWSER_FLOW-forms" ALTERNATIVE "Username and password or a passkey, then the second step"
    add_step "$BROWSER_FLOW-forms" auth-username-password-form REQUIRED
    add_subflow "$BROWSER_FLOW-forms" "$BROWSER_FLOW-second-step" CONDITIONAL "Authenticator or recovery code, if the user has one"
    add_step "$BROWSER_FLOW-second-step" conditional-user-configured REQUIRED
    add_step "$BROWSER_FLOW-second-step" conditional-credential REQUIRED
    # No second step after a passkey, as in the built-in flow
    configure_step "$BROWSER_FLOW-second-step" conditional-credential '{"alias":"gen9-browser-credential","config":{"credentials":"webauthn-passwordless"}}'
    add_step "$BROWSER_FLOW-second-step" auth-otp-form ALTERNATIVE
    add_step "$BROWSER_FLOW-second-step" auth-recovery-authn-code-form ALTERNATIVE
    admins_second_step "$BROWSER_FLOW-forms" "$BROWSER_FLOW-second-step"
    [[ $(shape_of "$BROWSER_FLOW") == "$BROWSER_SHAPE" ]] || { echo "sign-in: $BROWSER_FLOW came out as: $(shape_of "$BROWSER_FLOW")" >&2; exit 1; }
    echo "sign-in: $BROWSER_FLOW built"
  fi
  if [[ $(kcadm get realms/gen9 --fields browserFlow --format csv --noquotes) == "$BROWSER_FLOW" ]]; then
    echo "sign-in: bound to $BROWSER_FLOW"
  else
    kcadm update realms/gen9 -s browserFlow="$BROWSER_FLOW"
    echo "sign-in: now uses $BROWSER_FLOW"
  fi
}
browser_flow

# The seeded admin's authenticator app, so the checks can answer the second step admins need. Its
# secret is GEN9_SEED_ADMIN_OTP_SECRET in .env (make setup adds it to an older one), given when they
# have no app: one set up by hand is kept, and the checks then can't sign them in
seeded_admin_authenticator() {
  local id types
  id=$(kcadm get users -r gen9 -q exact=true -q "username=$GEN9_SEED_ADMIN_EMAIL" --fields id --format csv --noquotes)
  if [ -z "$id" ]; then
    echo "seeded admin: not in the realm"
  elif types=$(kcadm get "users/$id/credentials" -r gen9 --fields type --format csv --noquotes) && grep -qx otp <<<"$types"; then
    echo "seeded admin: has an authenticator app"
  else
    kcadm update "users/$id" -r gen9 -f - >/dev/null <<JSON
{"credentials": [{"type": "otp", "userLabel": "Gen9's checks", "secretData": "{\"value\":\"$GEN9_SEED_ADMIN_OTP_SECRET\"}",
  "credentialData": "{\"subType\":\"totp\",\"digits\":6,\"counter\":0,\"period\":30,\"algorithm\":\"HmacSHA1\"}"}]}
JSON
    echo "seeded admin: authenticator app added"
  fi
}
seeded_admin_authenticator

# An admin with no second step (an authenticator app, recovery codes or a passkey) is signed out:
# a Keycloak session from before asked for none, and their next sign-in sets one up. Gen9's admin
# API does the same when it makes someone admin (api/admin.py, SECOND_STEPS)
admins_signed_out_without_second_step() {
  local group ids id types out=0
  group=$(kcadm get groups -r gen9 -q exact=true -q search=admins --fields id --format csv --noquotes)
  # Admins through the group, or given the role directly (in Keycloak's console)
  ids=$({
    kcadm get "groups/$group/members" -r gen9 -q max=10000 --fields id --format csv --noquotes
    kcadm get roles/gen9-admin/users -r gen9 -q max=10000 --fields id --format csv --noquotes
  } | sort -u)
  for id in $ids; do
    types=$(kcadm get "users/$id/credentials" -r gen9 --fields type --format csv --noquotes)
    if ! grep -qx 'otp\|recovery-authn-codes\|webauthn-passwordless' <<<"$types"; then
      kcadm create "users/$id/logout" -r gen9
      out=$((out + 1))
    fi
  done
  echo "admins: $out with no second step, signed out (they set one up at their next sign-in)"
}
admins_signed_out_without_second_step

# Gen9 as an MCP server (gen9-agent's mcp_server.py): its tokens carry the server's URL as their
# audience, which MCP requires. Keycloak ignores RFC 8707's `resource` parameter, so the audience
# comes from a `gen9-mcp` client scope with an Audience mapper (Keycloak's "Integrating with
# Model Context Protocol"). The public client `gen9-mcp` is the pre-registered one for MCP clients
# (Claude Code's --client-id, VS Code, …): authorization code with PKCE (S256), loopback redirects
# on any port (RFC 8252), consent shown, and `gen9-mcp` among its default scopes.
# A client scope whose tokens carry $2 as their audience, bound to one of Gen9's endpoints for
# agents: prints its id. $1 its name, $3 what the consent screen says it allows
bound_scope() {
  local name=$1 audience=$2 consent=$3 scope mappers mapper
  # The image has no awk: the id is what comes before ",<name>"
  scope=$(kcadm get client-scopes -r gen9 --fields id,name --format csv --noquotes | grep ",$name$" || true)
  scope=${scope%%,*}
  if [[ -z $scope ]]; then
    scope=$(kcadm create client-scopes -r gen9 -s name="$name" -s protocol=openid-connect \
      -s 'attributes."include.in.token.scope"=true' -s 'attributes."display.on.consent.screen"=true' \
      -s "attributes.\"consent.screen.text\"=$consent" -i)
    echo "$name: client scope created" >&2
  else
    # What the consent page says follows this file on every start, not only the first
    kcadm update "client-scopes/$scope" -r gen9 -s "attributes.\"consent.screen.text\"=$consent"
  fi
  mappers=$(kcadm get "client-scopes/$scope/protocol-mappers/models" -r gen9 --fields id,name --format csv --noquotes)
  mapper=$(grep ",audience $name$" <<<"$mappers" || true)
  mapper=${mapper%%,*}
  if [[ -z $mapper ]]; then
    kcadm create "client-scopes/$scope/protocol-mappers/models" -r gen9 -f - >&2 <<JSON
{"name": "audience $name", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
 "config": {"included.custom.audience": "$audience", "access.token.claim": "true",
            "id.token.claim": "false", "introspection.token.claim": "true"}}
JSON
    echo "$name: audience mapper created" >&2
  else
    kcadm update "client-scopes/$scope/protocol-mappers/models/$mapper" -r gen9 \
      -s 'config."included.custom.audience"='"$audience"
  fi
  echo "$name: tokens' audience is $audience" >&2
  echo "$scope"
}

mcp_access() {
  local mcp a2a client
  mcp=$(bound_scope gen9-mcp "$GEN9_MCP_URL" "use Gen9 from this app: ask it, and read and search your chats")
  # Gen9 as an A2A agent (gen9-agent's a2a_server.py), the same way
  a2a=$(bound_scope gen9-a2a "$GEN9_A2A_URL" "work with Gen9 for you: send it tasks and read their results")

  client=$(client_id gen9-mcp)
  if [[ -z $client ]]; then
    client=$(kcadm create clients -r gen9 -s clientId=gen9-mcp -s name="Agents (MCP and A2A)" -s enabled=true \
      -s publicClient=true -s standardFlowEnabled=true -s directAccessGrantsEnabled=false \
      -s implicitFlowEnabled=false -s consentRequired=true -i)
    echo "gen9-mcp: client created"
  fi
  kcadm update "clients/$client" -r gen9 -s name="Agents (MCP and A2A)" \
    -s 'redirectUris=["http://127.0.0.1/*","http://localhost/*"]' -s 'webOrigins=["+"]' \
    -s 'attributes."pkce.code.challenge.method"=S256'
  kcadm get "clients/$client/default-client-scopes" -r gen9 --fields name --format csv --noquotes | grep -qx gen9-mcp ||
    kcadm update "clients/$client/default-client-scopes/$mcp" -r gen9
  kcadm get "clients/$client/optional-client-scopes" -r gen9 --fields name --format csv --noquotes | grep -qx gen9-a2a ||
    kcadm update "clients/$client/optional-client-scopes/$a2a" -r gen9
  echo "gen9-mcp: public client for agents, PKCE S256, loopback redirects, gen9-mcp by default, gen9-a2a on request"
}
mcp_access

# MCP clients that register themselves by the URL of their Client ID Metadata Document (the MCP
# spec's recommended registration; Keycloak's `cimd` feature). Keycloak fetches the document and
# keeps the client. A client policy lets documents in only from trusted domains, which must cover
# the redirect URIs' hosts too (gen9-agent/explore/mcp_server/NOTES.md). `gen9-mcp` becomes a
# realm default optional scope, so such a client gets the MCP audience by asking for it. This
# script owns the realm's client policies and profiles: it writes them whole. `gen9-a2a` too, for
# agents that call Gen9 over A2A.
mcp_clients() {
  local scope name domains="" d list scheme='"https"'
  # Adding one twice is a conflict (409), so only those not there yet
  for name in gen9-mcp gen9-a2a; do
    if ! kcadm get default-optional-client-scopes -r gen9 --fields name --format csv --noquotes | grep -qx "$name"; then
      scope=$(kcadm get client-scopes -r gen9 --fields id,name --format csv --noquotes | grep ",$name$" || true)
      kcadm update "default-optional-client-scopes/${scope%%,*}" -r gen9
      echo "$name: a realm default optional scope"
    fi
  done
  IFS=, read -ra list <<<"$GEN9_MCP_CLIENT_DOMAINS"
  for d in "${list[@]}"; do domains+="${domains:+,}\"${d// /}\""; done
  [[ $GEN9_MCP_CLIENT_ALLOW_HTTP == true ]] && scheme='"https","http"'
  # Every public client needs PKCE with S256 on each authorization and code exchange, as OAuth's
  # security BCP says (RFC 9700, 2.1.1; ASVS 5.0 10.4.6): those that register themselves too, which
  # are public. A policy of its own, as the `client-id-uri` condition votes only before the request
  # and abstains on it, where `pkce-enforcer` acts: in the MCP profile it never ran (manual-e2e.md,
  # P7-B1). A public client created or updated gets S256 set (auto-configure)
  kcadm update client-policies/profiles -r gen9 -f - <<JSON
{"profiles": [{"name": "mcp-clients", "description": "MCP clients by Client ID Metadata Document",
  "executors": [{"executor": "client-id-metadata-document", "configuration": {
    "cimd-allow-http-scheme": "$GEN9_MCP_CLIENT_ALLOW_HTTP", "cimd-allow-permitted-domains": [$domains],
    "cimd-restrict-same-domain": "false", "only-allow-confidential-client": "false"}}]},
  {"name": "pkce", "description": "PKCE with S256",
  "executors": [{"executor": "pkce-enforcer", "configuration": {"auto-configure": "true"}}]}]}
JSON
  kcadm update client-policies/policies -r gen9 -f - <<JSON
{"policies": [{"name": "mcp-clients", "enabled": true, "profiles": ["mcp-clients"],
  "conditions": [{"condition": "client-id-uri", "configuration": {
    "client-id-uri-scheme": [$scheme], "client-id-uri-allow-permitted-domains": [$domains]}}]},
  {"name": "public-clients", "enabled": true, "profiles": ["pkce"],
  "conditions": [{"condition": "client-access-type", "configuration": {"type": ["public"]}}]}]}
JSON
  echo "mcp clients: documents from $GEN9_MCP_CLIENT_DOMAINS (http allowed: $GEN9_MCP_CLIENT_ALLOW_HTTP); public clients: PKCE S256"
}
mcp_clients

# gen9-agent puts a person's own sign-in records in their data export (GDPR Art. 15), which reads
# Keycloak's events: view-events. The realm file gives it; this gives it to realms imported before
# (docs/plans/gen9-learn.md, M9, F14)
kcadm add-roles -r gen9 --uusername service-account-gen9-agent --cclientid realm-management --rolename view-events
echo "gen9-agent: reads sign-in records (view-events)"

# OAuth, as OWASP ASVS 5.0's V10.4 asks of an authorization server (docs/plans/manual-e2e.md, P7-B1)
# - No password grant: OAuth's security BCP forbids it (RFC 9700, 2.4). Keycloak gives every realm
#   an `admin-cli` client that allows it; Gen9's scripts sign in to the master realm's, so this
#   realm's goes without (10.4.4).
# - PKCE with S256 on every client of the code flow (10.4.6): Keycloak's own `account` client
#   didn't ask for it. temporal-ui stays without, since Temporal's UI sends no code_challenge
#   (temporalio/ui#2519): a confidential client that checks the ID token's nonce before using any
#   token, which RFC 9700 (2.1.1, 4.5.3.2) allows instead.
# - Offline tokens end 30 days after the sign-in that made them, however often they're used, as the
#   longest "remember me" sign-in does (10.4.8). Keycloak's default keeps them for as long as
#   they're used. The import sets it on first start; this brings older realms in line.
# - Only the scopes a client asks for (10.4.11): Gen9's clients ask for openid, profile and email,
#   or gen9-mcp and gen9-a2a, so Keycloak's address, phone, organization and microprofile-jwt go,
#   from them and from what a client that registers itself gets. offline_access stays only for
#   agents (gen9-mcp, and those that register themselves), which MCP lets ask for it.
UNASKED_SCOPES=(address phone organization microprofile-jwt)
# Takes the named scopes off a list of client scopes, where they are: $1 the list's path
# (default-optional-client-scopes, or clients/<id>/optional-client-scopes), then the names
drop_scopes() {
  local path=$1 line name
  shift
  while IFS= read -r line; do
    for name in "$@"; do
      if [[ ${line#*,} == "$name" ]]; then
        kcadm delete "$path/${line%%,*}" -r gen9
        echo "$path: $name taken off"
      fi
    done
  done < <(kcadm get "$path" -r gen9 --fields id,name --format csv --noquotes)
}
oauth() {
  local id client
  id=$(client_id admin-cli)
  kcadm update "clients/$id" -r gen9 -s directAccessGrantsEnabled=false
  id=$(client_id account)
  kcadm update "clients/$id" -r gen9 -s 'attributes."pkce.code.challenge.method"=S256'
  kcadm update realms/gen9 -s offlineSessionMaxLifespanEnabled=true -s offlineSessionMaxLifespan=2592000
  drop_scopes default-optional-client-scopes "${UNASKED_SCOPES[@]}"
  for client in gen9-ui gen9-cli temporal-ui gen9-agent; do
    drop_scopes "clients/$(client_id "$client")/optional-client-scopes" "${UNASKED_SCOPES[@]}" offline_access
  done
  drop_scopes "clients/$(client_id gen9-mcp)/optional-client-scopes" "${UNASKED_SCOPES[@]}"
  echo "oauth: no password grant, PKCE but temporal-ui, offline tokens for 30 days, only the scopes asked for"
}
oauth
