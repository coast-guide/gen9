<#ftl output_format="plainText">
<#--
  This file has been claimed for ownership from @keycloakify/email-native version 260007.0.0.
  To relinquish ownership and restore this file to its original content, run the following command:
  
  $ npx keycloakify own --path "email/text/event-update_credential.ftl" --revert
-->

<#--
  Gen9's own: a person is told when how they sign in changes (NIST SP 800-63B-4: when an
  authenticator is added, the CSP SHALL notify the subscriber; account recovery too), in words,
  where Keycloak's names the credential type (otp, webauthn-passwordless). docs/plans/manual-e2e.md,
  P6-C5.
-->
<#setting time_zone="UTC">
<#assign what = ({"password": "A password was set for", "otp": "An authenticator app was added to", "webauthn-passwordless": "A passkey was added to", "webauthn": "A security key was added to", "recovery-authn-codes": "New recovery codes were made for"})[event.getDetail("credential_type")!""]!"A way to sign in was added to">
${msg("eventUpdateCredentialBody", what, event.date?string("d MMMM yyyy, HH:mm 'UTC'"), event.ipAddress)}
