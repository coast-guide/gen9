import { kcSanitize } from "keycloakify/lib/kcSanitize";
import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import { useState } from "react";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Two-factor code (login-otp.ftl). Same fields as Keycloak expects (selectedCredentialId, otp,
 * login); tuned for phones: numeric keypad, OS one-time-code autofill, large spaced digits.
 */
export default function LoginOtp(props: PageProps<Extract<KcContext, { pageId: "login-otp.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { otpLogin, url, messagesPerField } = kcContext;
  const { msg, msgStr } = i18n;
  const [submitting, setSubmitting] = useState(false);
  const hasError = messagesPerField.existsError("totp");

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={!hasError}
      headerNode={msg("doLogIn")}
    >
      <form
        id="kc-otp-login-form"
        className="grid gap-5"
        action={url.loginAction}
        method="post"
        onSubmit={() => {
          setSubmitting(true);
          return true;
        }}
      >
        {otpLogin.userOtpCredentials.length > 1 && (
          <fieldset className="grid gap-2.5">
            <legend className="mb-2 text-sm font-medium">{msgStr("loginChooseAuthenticator")}</legend>
            {otpLogin.userOtpCredentials.map((credential, index) => (
              <label key={credential.id} className={kcClsx("kcLoginOTPListClass")}>
                <input
                  type="radio"
                  name="selectedCredentialId"
                  value={credential.id}
                  defaultChecked={credential.id === otpLogin.selectedCredentialId}
                  className={kcClsx("kcLoginOTPListInputClass")}
                  id={`kc-otp-credential-${index}`}
                />
                <span className={kcClsx("kcLoginOTPListItemIconBodyClass")}>
                  <span className={kcClsx("kcLoginOTPListItemIconClass")} aria-hidden />
                </span>
                <span className={kcClsx("kcLoginOTPListItemTitleClass")}>{credential.userLabel}</span>
              </label>
            ))}
          </fieldset>
        )}

        <div className="grid gap-2">
          <label htmlFor="otp" className={kcClsx("kcLabelClass")}>
            {msg("loginOtpOneTime")}
          </label>
          <input
            id="otp"
            name="otp"
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete="one-time-code"
            autoFocus
            className={`${kcClsx("kcInputClass")} h-14 text-center font-mono text-2xl tracking-[0.5em]`}
            aria-invalid={hasError}
            aria-describedby={hasError ? "input-error-otp-code" : "otp-hint"}
          />
          {hasError ? (
            <p
              id="input-error-otp-code"
              className={kcClsx("kcInputErrorMessageClass")}
              aria-live="polite"
              dangerouslySetInnerHTML={{ __html: kcSanitize(messagesPerField.get("totp")) }}
            />
          ) : (
            <p id="otp-hint" className="text-sm text-muted-foreground">
              {msg("loginOtpHint")}
            </p>
          )}
        </div>

        <button
          id="kc-login"
          name="login"
          type="submit"
          disabled={submitting}
          className={kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass")}
        >
          {msgStr("doLogIn")}
        </button>
      </form>
    </Template>
  );
}
