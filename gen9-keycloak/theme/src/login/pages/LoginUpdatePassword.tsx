import { kcSanitize } from "keycloakify/lib/kcSanitize";
import { getKcClsx, type KcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { JSX } from "keycloakify/tools/JSX";
import { useIsPasswordRevealed } from "keycloakify/tools/useIsPasswordRevealed";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Choose a password (login-update-password.ftl), Keycloakify's page with two changes:
 * - a new account's first password asks for "Password" and offers no "Sign out of other devices",
 *   which it has none of (manual-e2e.md, P3-D3). Keycloak says `updatePasswordMessage` only when the
 *   account itself carries UPDATE_PASSWORD, as sign-up leaves it (RegistrationPassword); a reset, an
 *   admin's email and Settings' change say `resetPasswordMessage` (FreeMarkerLoginFormsProvider,
 *   26.7.4). A refused try comes back with its error instead, so the page remembers, for this
 *   sign-in's tab (Keycloak's `tab_id`), that it was a first password (gen9-learn.md, M9, F2);
 * - the rules come before the first try: `gen9PasswordHint`, which says the realm's policy
 *   (realm/gen9-realm.json; verify.sh fails when the two differ).
 */
export default function LoginUpdatePassword(props: PageProps<Extract<KcContext, { pageId: "login-update-password.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;

  const { kcClsx } = getKcClsx({
    doUseDefaultCss,
    classes
  });

  const { msg, msgStr } = i18n;

  const { url, messagesPerField, isAppInitiatedAction, message } = kcContext;
  const firstPassword = !isAppInitiatedAction && (message?.summary === msgStr("updatePasswordMessage") || rememberedFirstPassword());
  if (firstPassword) rememberFirstPassword();
  const passwordError = messagesPerField.existsError("password");

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={!messagesPerField.existsError("password", "password-confirm")}
      headerNode={msg("updatePasswordTitle")}
    >
      <form id="kc-passwd-update-form" className={kcClsx("kcFormClass")} action={url.loginAction} method="post">
        <div className={kcClsx("kcFormGroupClass")}>
          <div className={kcClsx("kcLabelWrapperClass")}>
            <label htmlFor="password-new" className={kcClsx("kcLabelClass")}>
              {msg(firstPassword ? "password" : "passwordNew")}
            </label>
          </div>
          <div className={kcClsx("kcInputWrapperClass")}>
            <PasswordWrapper kcClsx={kcClsx} i18n={i18n} passwordInputId="password-new">
              <input
                type="password"
                id="password-new"
                name="password-new"
                className={kcClsx("kcInputClass")}
                autoFocus
                autoComplete="new-password"
                aria-invalid={messagesPerField.existsError("password", "password-confirm")}
                aria-describedby={passwordError ? "input-error-password" : "password-hint"}
              />
            </PasswordWrapper>

            {!passwordError && (
              <p id="password-hint" className="mt-1.5 text-sm text-muted-foreground">
                {msg("gen9PasswordHint")}
              </p>
            )}

            {passwordError && (
              <span
                id="input-error-password"
                className={kcClsx("kcInputErrorMessageClass")}
                aria-live="polite"
                dangerouslySetInnerHTML={{
                  __html: kcSanitize(messagesPerField.get("password"))
                }}
              />
            )}
          </div>
        </div>

        <div className={kcClsx("kcFormGroupClass")}>
          <div className={kcClsx("kcLabelWrapperClass")}>
            <label htmlFor="password-confirm" className={kcClsx("kcLabelClass")}>
              {msg("passwordConfirm")}
            </label>
          </div>
          <div className={kcClsx("kcInputWrapperClass")}>
            <PasswordWrapper kcClsx={kcClsx} i18n={i18n} passwordInputId="password-confirm">
              <input
                type="password"
                id="password-confirm"
                name="password-confirm"
                className={kcClsx("kcInputClass")}
                autoComplete="new-password"
                aria-invalid={messagesPerField.existsError("password", "password-confirm")}
              />
            </PasswordWrapper>

            {messagesPerField.existsError("password-confirm") && (
              <span
                id="input-error-password-confirm"
                className={kcClsx("kcInputErrorMessageClass")}
                aria-live="polite"
                dangerouslySetInnerHTML={{
                  __html: kcSanitize(messagesPerField.get("password-confirm"))
                }}
              />
            )}
          </div>
        </div>
        <div className={kcClsx("kcFormGroupClass")}>
          {!firstPassword && <LogoutOtherSessions kcClsx={kcClsx} i18n={i18n} />}
          <div id="kc-form-buttons" className={kcClsx("kcFormButtonsClass")}>
            <input
              className={kcClsx(
                "kcButtonClass",
                "kcButtonPrimaryClass",
                !isAppInitiatedAction && "kcButtonBlockClass",
                "kcButtonLargeClass"
              )}
              type="submit"
              value={msgStr("doSubmit")}
            />
            {isAppInitiatedAction && (
              <button
                className={kcClsx("kcButtonClass", "kcButtonDefaultClass", "kcButtonLargeClass")}
                type="submit"
                name="cancel-aia"
                value="true"
              >
                {msg("doCancel")}
              </button>
            )}
          </div>
        </div>
      </form>
    </Template>
  );
}

// This sign-in's tab, as Keycloak names it in every page's address; the flag lives for the tab only
const FIRST_PASSWORD_KEY = () => `gen9-first-password:${new URLSearchParams(window.location.search).get("tab_id") ?? ""}`;

function rememberedFirstPassword(): boolean {
  try {
    return window.sessionStorage.getItem(FIRST_PASSWORD_KEY()) === "1";
  } catch {
    return false;
  }
}

function rememberFirstPassword() {
  try {
    window.sessionStorage.setItem(FIRST_PASSWORD_KEY(), "1");
  } catch {
    // Storage refused (a private window may): the page falls back to Keycloak's message alone
  }
}

function LogoutOtherSessions(props: { kcClsx: KcClsx; i18n: I18n }) {
  const { kcClsx, i18n } = props;

  const { msg } = i18n;

  return (
    <div id="kc-form-options" className={kcClsx("kcFormOptionsClass")}>
      <div className={kcClsx("kcFormOptionsWrapperClass")}>
        <div className="checkbox">
          <label>
            <input type="checkbox" id="logout-sessions" name="logout-sessions" value="on" />
            {msg("logoutOtherSessions")}
          </label>
        </div>
      </div>
    </div>
  );
}

function PasswordWrapper(props: { kcClsx: KcClsx; i18n: I18n; passwordInputId: string; children: JSX.Element }) {
  const { kcClsx, i18n, passwordInputId, children } = props;

  const { msgStr } = i18n;

  const { isPasswordRevealed, toggleIsPasswordRevealed } = useIsPasswordRevealed({ passwordInputId });

  return (
    <div className={kcClsx("kcInputGroup")}>
      {children}
      <button
        type="button"
        className={kcClsx("kcFormPasswordVisibilityButtonClass")}
        aria-label={msgStr(isPasswordRevealed ? "hidePassword" : "showPassword")}
        aria-controls={passwordInputId}
        onClick={toggleIsPasswordRevealed}
      >
        <i className={kcClsx(isPasswordRevealed ? "kcFormPasswordVisibilityIconHide" : "kcFormPasswordVisibilityIconShow")} aria-hidden />
      </button>
    </div>
  );
}
