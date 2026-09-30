import type { PageProps } from "keycloakify/login/pages/PageProps";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Verify your email (login-verify-email.ftl). Keycloakify's page asks about "a verification code"
 * (the email holds a link) and resends with "Click here"; this one says what the email is and
 * names the link by what it does (WCAG 2.4.4). Found by hand: docs/plans/manual-e2e.md, P2-J4.
 */
export default function LoginVerifyEmail(props: PageProps<Extract<KcContext, { pageId: "login-verify-email.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { msg } = i18n;
  const { url, user } = kcContext;

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayInfo
      headerNode={msg("emailVerifyTitle")}
      infoNode={
        <p className="instruction">
          {msg("emailVerifyResendQuestion")}{" "}
          <a href={url.loginAction} className="font-medium text-link underline-offset-4 hover:underline">
            {msg("emailVerifyResend")}
          </a>
        </p>
      }
    >
      <p className="instruction">{msg("emailVerifyInstruction1", user?.email ?? "")}</p>
    </Template>
  );
}
