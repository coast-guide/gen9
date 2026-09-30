import { i18nBuilder } from "keycloakify/login";

import type { ThemeName } from "../kc.gen";

/**
 * Gen9's voice on Keycloak's pages: sentence case, plain verbs, the product's name.
 * Only overrides; every other string comes from Keycloak's default translations.
 * @see https://docs.keycloakify.dev/features/i18n
 */
const { useI18n, ofTypeI18n } = i18nBuilder
  .withThemeName<ThemeName>()
  .withCustomTranslations({
    en: {
      loginTitle: "Sign in to Gen9",
      loginAccountTitle: "Sign in to Gen9",
      registerTitle: "Create your Gen9 account",
      // The first layer of what a person is told at sign-up, before their data is collected (GDPR
      // Art. 13; WP260 rev.01: the purposes, the rights, and what could surprise them), and the link
      // to the rest (gen9-ui's /privacy)
      gen9RegisterPrivacy: "Gen9 is an AI system. What you ask it goes to AI model providers, some outside the EU, that don’t train on it.",
      gen9PrivacyLink: "How Gen9 uses your data",
      noAccount: "New to Gen9?",
      doRegister: "Create an account",
      doLogIn: "Sign in",
      doSubmit: "Continue",
      doForgotPassword: "Forgot password?",
      emailForgotTitle: "Reset your password",
      emailInstruction: "Enter the email you use for Gen9. We’ll send you a link to choose a new password.",
      backToLogin: "Back to sign in",
      updatePasswordTitle: "Choose a password",
      loginTotpTitle: "Set up an authenticator app",
      loginProfileTitle: "Update your profile",
      emailVerifyTitle: "Verify your email",
      // A new account's first step reads as what it is (P2-J4): a link in an email, sent again on request
      emailVerifyInstruction1: "We sent a link to {0}. Open it to verify your email and finish creating your account.",
      emailVerifyResendQuestion: "Didn’t get the email?",
      emailVerifyResend: "Send it again",
      // The button is Continue (doSubmit), and "OTP devices" is jargon
      loginTotpStep3: "Enter the code the app shows, then Continue.",
      loginTotpStep3DeviceName: "Name this device, so you can tell your authenticator apps apart later.",
      pageExpiredTitle: "This page has expired",
      errorTitle: "Something went wrong",
      logoutConfirmTitle: "Sign out",
      logoutConfirmHeader: "Sign out of Gen9?",
      doLogout: "Sign out",
      restartLoginTooltip: "Use a different account",
      reauthenticate: "Confirm it’s you to continue.",
      passwordNew: "New password",
      passwordNewConfirm: "Confirm new password",
      doTryAnotherWay: "Try another way",
      requiredFields: "Required",
      "passkey-doAuthenticate": "Sign in with a passkey",
      "webauthn-doAuthenticate": "Sign in with a passkey",
      "webauthn-registration-title": "Add a passkey",
      passkeyRegisterIntro: "Sign in with your face, fingerprint or screen lock instead of your password.",
      passkeyNameLabel: "Passkey name",
      passkeyNameHint: "Helps you tell your passkeys apart.",
      doRegisterSecurityKey: "Create passkey",
      logoutOtherSessions: "Sign out of other devices",
      "webauthn-error-title": "Passkey didn’t work",
      "webauthn-error-registration": "Your passkey wasn’t added. {0}",
      "webauthn-error-api-get": "Couldn’t sign in with your passkey. {0}",
      // Choosing another way to sign in, and recovery codes (Keycloak accepts only the next unused code)
      loginChooseAuthenticator: "Choose how to sign in",
      "otp-display-name": "Authenticator app",
      "otp-help-text": "Enter a code from your authenticator app.",
      "webauthn-passwordless-help-text": "Use your face, fingerprint or screen lock.",
      "webauthn-help-text": "Use your passkey or security key.",
      "password-help-text": "Enter your password.",
      "recovery-authn-codes-display-name": "Recovery code",
      "recovery-authn-codes-help-text": "Use one of the codes you saved.",
      "requiredAction.CONFIGURE_RECOVERY_AUTHN_CODES": "Save recovery codes",
      "auth-recovery-code-header": "Enter a recovery code",
      "auth-recovery-code-prompt": "Recovery code {0}",
      recoveryCodeHint: "Codes work in order, once each: enter number {0} from your saved list.",
      "recovery-codes-error-invalid": "That code didn’t work. Check that it’s the right number and try again.",
      "recovery-code-config-header": "Save your recovery codes",
      "recovery-code-config-warning-title": "You won’t see these codes again.",
      "recovery-code-config-warning-message":
        "If you can’t use your authenticator app, a recovery code gets you in. Each works once, in order. Keep them in a password manager or print them.",
      "recovery-codes-confirmation-message": "I’ve saved these codes",
      "recovery-codes-action-complete": "Use these codes",
      "recovery-codes-action-cancel": "Cancel",
      "recovery-codes-download-file-header": "Gen9 recovery codes",
      "recovery-codes-download-file-description":
        "Each code works once, in order. Use one to sign in to Gen9 when you can’t use your authenticator app.",
      "recovery-codes-download-file-date": "Created",
      // App consent (Gen9 CLI) and signing in a device with a code (OAuth 2.0 Device Authorization Grant)
      oauthGrantTitle: "Allow {0} to use your account?",
      oauthGrantRequest: "It will be able to:",
      oauthGrantOnlyIfYou:
        "Only allow this if you started signing in to {0} yourself, just now. If someone sent you a link or a code, choose Don’t allow: it would give them your account.",
      oauthGrantAllow: "Allow",
      profileScopeConsentText: "see your name",
      emailScopeConsentText: "see your email address",
      rolesScopeConsentText: "see your role in Gen9 (member or admin)",
      offlineAccessScopeConsentText: "use your account while you’re not signed in",
      oauthGrantDeny: "Don’t allow",
      oauth2DeviceVerificationTitle: "Sign in a device",
      verifyOAuth2DeviceUserCode: "Enter the code shown on your device.",
      oauth2DeviceInvalidUserCodeMessage: "That code didn’t work. Check it and try again.",
      oauth2DeviceExpiredUserCodeMessage: "That code has expired. Start signing in again on your device.",
      oauth2DeviceVerificationCompleteHeader: "Your device is signed in",
      oauth2DeviceVerificationCompleteMessage: "You can close this window and go back to your device.",
      oauth2DeviceVerificationFailedHeader: "Your device wasn’t signed in",
      oauth2DeviceVerificationFailedMessage: "Go back to your device and start signing in again.",
      oauth2DeviceConsentDeniedMessage: "You didn’t allow the device to use your account.",
      deleteCredentialTitle: "Remove {0}?",
      deleteCredentialMessage: "You won’t be able to use it to sign in to Gen9. You can add it again from Settings.",
      doConfirmDelete: "Remove",
      "webauthn-unsupported-browser-text": "This browser can’t use passkeys. Try another browser, or sign in with your password.",
      backToApplication: "Back to Gen9",
      proceedWithAction: "Continue",
      continueToGen9: "Continue to Gen9",
      // Temporal's web UI turns away anyone without gen9-admin (config/configure.sh, the
      // gen9-temporal-ui flow's Deny Access step names this key); its page says so (pages/Error.tsx)
      gen9TemporalAdminsOnlyTitle: "Temporal is for admins",
      gen9TemporalAdminsOnly: "Temporal is for Gen9 admins. If you need it, ask one of your admins.",
      loginOtpHint: "Enter the current 6-digit code from your authenticator app.",
      confirmEmailAddressVerificationHeader: "Confirm your email",
      confirmEmailAddressVerification: "Confirm that {0} is your email address.",
      confirmExecutionOfActions: "Complete these steps to continue:",
      // Errors say what happened and how to fix it (Keycloak's defaults read like log lines)
      invalidUserMessage: "That email and password don’t match. Try again or reset your password.",
      invalidPasswordBlacklistedMessage: "That password is too common. Choose one that’s harder to guess.",
      invalidPasswordMinLengthMessage: "Use at least {0} characters.",
      invalidPasswordMaxLengthMessage: "Use at most {0} characters.",
      invalidPasswordNotUsernameMessage: "Your password can’t be your email or username.",
      invalidPasswordNotEmailMessage: "Your password can’t be your email.",
      notMatchPasswordMessage: "The passwords don’t match.",
      invalidTotpMessage: "That code didn’t work. Enter the current code from your authenticator app.",
      missingTotpMessage: "Enter the code from your authenticator app.",
      invalidEmailMessage: "Enter a valid email address.",
      emailExistsMessage: "An account with this email already exists. Sign in instead.",
      accountDisabledMessage: "This account is disabled. Contact your Gen9 admin.",
      emailSentMessage: "Check your email. If an account exists for it, we sent a link.",
      expiredActionMessage: "That step took too long. Continue signing in.",
      loginTimeout: "Your sign-in timed out. Start again.",
      verifyEmailMessage: "Verify your email address to finish setting up your account.",
      updatePasswordMessage: "Choose a password to continue.",
      // Says realm/gen9-realm.json's passwordPolicy, before a first try; verify.sh fails when they differ
      gen9PasswordHint: "At least 15 characters. Not a common password, and not your email.",
      configureTotpMessage: "Set up an authenticator app to continue.",
      resetPasswordMessage: "Choose a new password for your account.",
      accountTemporarilyDisabledMessage: "Too many sign-in attempts. Wait a few minutes and try again, or reset your password.",
    },
  })
  .build();

type I18n = typeof ofTypeI18n;

export { type I18n, useI18n };
