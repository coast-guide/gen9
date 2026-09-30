import { kcSanitize } from "keycloakify/lib/kcSanitize";
import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import { useScript } from "keycloakify/login/pages/Login.useScript";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import { useIsPasswordRevealed } from "keycloakify/tools/useIsPasswordRevealed";
import { type ReactNode, useState } from "react";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Sign in (login.ftl). Same fields, names and passkey wiring as Keycloakify's page (Keycloak
 * reads them), laid out for Gen9: email, password with reveal, remember me + reset on one line,
 * one primary action, and passkeys when the realm offers them.
 */
export default function Login(props: PageProps<Extract<KcContext, { pageId: "login.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { realm, url, usernameHidden, login, auth, registrationDisabled, messagesPerField, enableWebAuthnConditionalUI, authenticators } =
    kcContext;
  const { msg, msgStr } = i18n;
  const [submitting, setSubmitting] = useState(false);

  const webAuthnButtonId = "authenticateWebAuthnButton";
  useScript({ webAuthnButtonId, kcContext, i18n });

  const hasError = messagesPerField.existsError("username", "password");
  const error = hasError ? kcSanitize(messagesPerField.getFirstError("username", "password")) : null;

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={!hasError}
      headerNode={msg("loginAccountTitle")}
      displayInfo={realm.password && realm.registrationAllowed && !registrationDisabled}
      infoNode={
        <span>
          {msg("noAccount")}{" "}
          <a href={url.registrationUrl} className="font-medium text-link underline-offset-4 hover:underline">
            {msg("doRegister")}
          </a>
        </span>
      }
    >
      {realm.password && (
        <form
          id="kc-form-login"
          className="grid gap-5"
          action={url.loginAction}
          method="post"
          onSubmit={() => {
            setSubmitting(true);
            return true;
          }}
        >
          {!usernameHidden && (
            <div className="grid gap-2">
              <label htmlFor="username" className={kcClsx("kcLabelClass")}>
                {!realm.loginWithEmailAllowed ? msg("username") : !realm.registrationEmailAsUsername ? msg("usernameOrEmail") : msg("email")}
              </label>
              <input
                id="username"
                name="username"
                className={kcClsx("kcInputClass")}
                defaultValue={login.username ?? ""}
                type={realm.registrationEmailAsUsername ? "email" : "text"}
                inputMode={realm.registrationEmailAsUsername ? "email" : undefined}
                autoCapitalize="none"
                spellCheck={false}
                autoFocus
                autoComplete={enableWebAuthnConditionalUI ? "username webauthn" : "username"}
                aria-invalid={hasError}
                aria-describedby={hasError ? "input-error" : undefined}
              />
            </div>
          )}

          <div className="grid gap-2">
            <label htmlFor="password" className={kcClsx("kcLabelClass")}>
              {msg("password")}
            </label>
            <PasswordReveal kcClsx={kcClsx} i18n={i18n} inputId="password">
              <input
                id="password"
                name="password"
                type="password"
                className={kcClsx("kcInputClass")}
                autoComplete="current-password"
                aria-invalid={hasError}
                aria-describedby={hasError ? "input-error" : undefined}
              />
            </PasswordReveal>
            {error && (
              <p id="input-error" className={kcClsx("kcInputErrorMessageClass")} aria-live="polite" dangerouslySetInnerHTML={{ __html: error }} />
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            {realm.rememberMe && !usernameHidden ? (
              <label className="inline-flex cursor-pointer items-center gap-2.5">
                <input id="rememberMe" name="rememberMe" type="checkbox" defaultChecked={!!login.rememberMe} />
                {msg("rememberMe")}
              </label>
            ) : (
              <span />
            )}
            {realm.resetPasswordAllowed && (
              <a href={url.loginResetCredentialsUrl} className="font-medium text-link underline-offset-4 hover:underline">
                {msg("doForgotPassword")}
              </a>
            )}
          </div>

          <input type="hidden" id="id-hidden-input" name="credentialId" value={auth.selectedCredential} />
          <button
            id="kc-login"
            name="login"
            type="submit"
            disabled={submitting}
            className={kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass")}
          >
            {msg("doLogIn")}
          </button>
        </form>
      )}

      {enableWebAuthnConditionalUI && (
        <>
          <form id="webauth" action={url.loginAction} method="post">
            <input type="hidden" id="clientDataJSON" name="clientDataJSON" />
            <input type="hidden" id="authenticatorData" name="authenticatorData" />
            <input type="hidden" id="signature" name="signature" />
            <input type="hidden" id="credentialId" name="credentialId" />
            <input type="hidden" id="userHandle" name="userHandle" />
            <input type="hidden" id="error" name="error" />
          </form>
          {authenticators !== undefined && authenticators.authenticators.length !== 0 && (
            <form id="authn_select">
              {authenticators.authenticators.map((authenticator, i) => (
                <input key={i} type="hidden" name="authn_use_chk" readOnly value={authenticator.credentialId} />
              ))}
            </form>
          )}
          <div className="mt-5 flex items-center gap-3 text-xs text-muted-foreground" aria-hidden>
            <span className="h-px flex-1 bg-border" />
            or
            <span className="h-px flex-1 bg-border" />
          </div>
          <button
            id={webAuthnButtonId}
            type="button"
            className={`${kcClsx("kcButtonClass", "kcButtonDefaultClass", "kcButtonBlockClass")} mt-5`}
          >
            <span className="gen9-icon gen9-icon-key" aria-hidden />
            {msgStr("passkey-doAuthenticate")}
          </button>
        </>
      )}
    </Template>
  );
}

/** The reveal toggle lives in a child so its effect runs once the input exists (Template renders late). */
function PasswordReveal(props: {
  kcClsx: ReturnType<typeof getKcClsx>["kcClsx"];
  i18n: I18n;
  inputId: string;
  children: ReactNode;
}) {
  const { kcClsx, i18n, inputId, children } = props;
  const { isPasswordRevealed, toggleIsPasswordRevealed } = useIsPasswordRevealed({ passwordInputId: inputId });
  return (
    <div className={kcClsx("kcInputGroup")}>
      {children}
      <button
        type="button"
        className={kcClsx("kcFormPasswordVisibilityButtonClass")}
        aria-label={i18n.msgStr(isPasswordRevealed ? "hidePassword" : "showPassword")}
        aria-controls={inputId}
        onClick={toggleIsPasswordRevealed}
      >
        <span className={kcClsx(isPasswordRevealed ? "kcFormPasswordVisibilityIconHide" : "kcFormPasswordVisibilityIconShow")} aria-hidden />
      </button>
    </div>
  );
}
