import { kcSanitize } from "keycloakify/lib/kcSanitize";
import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/** Forgot password (login-reset-password.ftl): say what happens first, then one field, one action. */
export default function LoginResetPassword(props: PageProps<Extract<KcContext, { pageId: "login-reset-password.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { url, realm, auth, messagesPerField } = kcContext;
  const { msg } = i18n;
  const hasError = messagesPerField.existsError("username");

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={!hasError}
      headerNode={msg("emailForgotTitle")}
    >
      <p className="-mt-2 mb-6 text-[0.9375rem] leading-relaxed text-muted-foreground">
        {realm.duplicateEmailsAllowed ? msg("emailInstructionUsername") : msg("emailInstruction")}
      </p>
      <form id="kc-reset-password-form" className="grid gap-5" action={url.loginAction} method="post">
        <div className="grid gap-2">
          <label htmlFor="username" className={kcClsx("kcLabelClass")}>
            {!realm.loginWithEmailAllowed ? msg("username") : !realm.registrationEmailAsUsername ? msg("usernameOrEmail") : msg("email")}
          </label>
          <input
            type={realm.registrationEmailAsUsername ? "email" : "text"}
            id="username"
            name="username"
            className={kcClsx("kcInputClass")}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            defaultValue={auth.attemptedUsername ?? ""}
            aria-invalid={hasError}
            aria-describedby={hasError ? "input-error-username" : undefined}
          />
          {hasError && (
            <p
              id="input-error-username"
              className={kcClsx("kcInputErrorMessageClass")}
              aria-live="polite"
              dangerouslySetInnerHTML={{ __html: kcSanitize(messagesPerField.get("username")) }}
            />
          )}
        </div>
        <button type="submit" className={kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass")}>
          {msg("doSubmit")}
        </button>
        <a href={url.loginUrl} className="justify-self-center text-sm font-medium text-link underline-offset-4 hover:underline">
          {msg("backToLogin")}
        </a>
      </form>
    </Template>
  );
}
