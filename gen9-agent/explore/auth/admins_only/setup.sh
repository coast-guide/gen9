#!/usr/bin/env bash
# P2-E3 probe: a client only admins may sign in to, on a throwaway Keycloak (the gen9-keycloak
# image, start-dev). Run inside it:  docker exec -i kc-deny-probe bash < setup.sh
# A realm `probe` (alice in `admins`, which holds realm role gen9-admin; bob not), a confidential
# client `probe-ui` bound to the flow below, and a public client `other` to have an SSO session.
set -euo pipefail
kcadm() { /opt/keycloak/bin/kcadm.sh "$@" --config /tmp/kcadm.config; }
kcadm config credentials --server http://localhost:8080 --realm master --user probe --password probe-not-secret >/dev/null
R=probe
kcadm create realms -s realm=$R -s enabled=true >/dev/null
kcadm create roles -r $R -s name=gen9-admin >/dev/null
kcadm create groups -r $R -s name=admins >/dev/null
kcadm add-roles -r $R --gname admins --rolename gen9-admin
for u in alice bob; do
  kcadm create users -r $R -s username=$u -s email=$u@probe.test -s firstName=$u -s lastName=Probe -s enabled=true -s emailVerified=true >/dev/null
  kcadm set-password -r $R --username $u --new-password "$u-probe-not-secret"
done
kcadm update "users/$(kcadm get users -r $R -q username=alice --fields id --format csv --noquotes)/groups/$(kcadm get groups -r $R -q search=admins --fields id --format csv --noquotes)" -r $R -s realm=$R -s userId=x -s groupId=x -n
kcadm create clients -r $R -s clientId=probe-ui -s enabled=true -s publicClient=false -s secret=probe-ui-not-secret \
  -s standardFlowEnabled=true -s 'redirectUris=["http://127.0.0.1:18098/cb"]' >/dev/null
kcadm create clients -r $R -s clientId=other -s enabled=true -s publicClient=true \
  -s standardFlowEnabled=true -s 'redirectUris=["http://127.0.0.1:18098/other"]' >/dev/null

# The flow: sign in as the realm's browser flow does (cookie, or the forms with their conditional
# second step), then, for anyone without gen9-admin, Deny Access with a message
FLOW=admins-only-browser
set_requirement() { # flow, provider or sub-flow name, requirement (a direct child of the flow)
  local id="" priority="" step provider name prio level
  while IFS=, read -r step provider name prio level; do
    if [[ ($provider == "$2" || $name == "$2") && $level == 0 ]]; then id=$step priority=$prio; fi
  done < <(kcadm get "authentication/flows/$1/executions" -r $R --fields id,providerId,displayName,priority,level --format csv --noquotes)
  kcadm update "authentication/flows/$1/executions" -r $R -b "{\"id\":\"$id\",\"requirement\":\"$3\",\"priority\":$priority}"
}
add_step() { kcadm create "authentication/flows/$1/executions/execution" -r $R -s provider="$2" -i >/dev/null; set_requirement "$1" "$2" "$3"; }
add_flow() { kcadm create "authentication/flows/$1/executions/flow" -r $R -s alias="$2" -s type=basic-flow -s description="$4" -i >/dev/null; set_requirement "$1" "$2" "$3"; }
configure() { # flow, provider, config JSON: the step's config
  local id step provider
  while IFS=, read -r step provider; do [[ $provider == "$2" ]] && id=$step; done < <(kcadm get "authentication/flows/$1/executions" -r $R --fields id,providerId --format csv --noquotes)
  kcadm create "authentication/executions/$id/config" -r $R -b "$3" >/dev/null
}
kcadm create authentication/flows -r $R -s alias=$FLOW -s providerId=basic-flow -s topLevel=true -s builtIn=false -s description="Sign in, admins only" >/dev/null
add_flow $FLOW "$FLOW-sign-in" REQUIRED "Cookie, or the forms"
add_step "$FLOW-sign-in" auth-cookie ALTERNATIVE
add_flow "$FLOW-sign-in" "$FLOW-forms" ALTERNATIVE "Username, password and a second step"
add_step "$FLOW-forms" auth-username-password-form REQUIRED
add_flow "$FLOW-forms" "$FLOW-second-step" CONDITIONAL "Authenticator or recovery code, if the user has one"
add_step "$FLOW-second-step" conditional-user-configured REQUIRED
add_step "$FLOW-second-step" conditional-credential REQUIRED
configure "$FLOW-second-step" conditional-credential '{"alias":"admins-only-credential","config":{"credentials":"webauthn-passwordless"}}'
add_step "$FLOW-second-step" auth-otp-form ALTERNATIVE
add_step "$FLOW-second-step" auth-recovery-authn-code-form ALTERNATIVE
add_flow $FLOW "$FLOW-not-admin" CONDITIONAL "Anyone without gen9-admin is turned away"
add_step "$FLOW-not-admin" conditional-user-role REQUIRED
configure "$FLOW-not-admin" conditional-user-role '{"alias":"admins-only-role","config":{"condUserRole":"gen9-admin","negate":"true"}}'
add_step "$FLOW-not-admin" deny-access-authenticator REQUIRED
configure "$FLOW-not-admin" deny-access-authenticator '{"alias":"admins-only-deny","config":{"denyErrorMessage":"Only Gen9 admins can open this."}}'
kcadm update "clients/$(kcadm get clients -r $R -q clientId=probe-ui --fields id --format csv --noquotes)" -r $R \
  -s "authenticationFlowBindingOverrides.browser=$(kcadm get authentication/flows -r $R --fields id,alias --format csv --noquotes | grep ",$FLOW\$" | cut -d, -f1)"
kcadm get "authentication/flows/$FLOW/executions" -r $R --fields level,providerId,displayName,requirement --format csv --noquotes
