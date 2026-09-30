import { kcSanitize } from "keycloakify/lib/kcSanitize";
import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import { useState } from "react";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Sign in with a recovery code (login-recovery-authn-code-input.ftl). Keycloak accepts only the
 * next unused code, so the page leads with its number.
 */
export default function LoginRecoveryAuthnCodeInput(
  props: PageProps<Extract<KcContext, { pageId: "login-recovery-authn-code-input.ftl" }>, I18n>,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { url, messagesPerField, recoveryAuthnCodesInputBean } = kcContext;
  const { msg, msgStr } = i18n;
  const [submitting, setSubmitting] = useState(false);
  const number = `${recoveryAuthnCodesInputBean.codeNumber}`;
  const hasError = messagesPerField.existsError("recoveryCodeInput");

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={!hasError}
      headerNode={msg("auth-recovery-code-header")}
    >
      <form
        id="kc-recovery-code-login-form"
        className="grid gap-5"
        action={url.loginAction}
        method="post"
        onSubmit={() => {
          setSubmitting(true);
          return true;
        }}
      >
        <div className="grid gap-2">
          <label htmlFor="recoveryCodeInput" className={kcClsx("kcLabelClass")}>
            {msg("auth-recovery-code-prompt", number)}
          </label>
          <input
            id="recoveryCodeInput"
            name="recoveryCodeInput"
            type="text"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            className={`${kcClsx("kcInputClass")} font-mono tracking-wide`}
            aria-invalid={hasError}
            aria-describedby={hasError ? "input-error" : "recovery-code-hint"}
          />
          {hasError ? (
            <p
              id="input-error"
              className={kcClsx("kcInputErrorMessageClass")}
              aria-live="polite"
              dangerouslySetInnerHTML={{ __html: kcSanitize(messagesPerField.get("recoveryCodeInput")) }}
            />
          ) : (
            <p id="recovery-code-hint" className="text-sm text-muted-foreground">
              {msg("recoveryCodeHint", number)}
            </p>
          )}
        </div>

        <button id="kc-login" name="login" type="submit" disabled={submitting} className={kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass")}>
          {msgStr("doLogIn")}
        </button>
      </form>
    </Template>
  );
}
