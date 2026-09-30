import { kcSanitize } from "keycloakify/lib/kcSanitize";
import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Info (info.ftl): status pages such as "Account updated" or "Confirm your email". Same decisions
 * as Keycloakify's page; the next step is a real button, and when Keycloak offers no way back
 * (e.g. an admin-sent link without a client) it points to Gen9 instead of a dead end.
 */
export default function Info(props: PageProps<Extract<KcContext, { pageId: "info.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { advancedMsgStr, msg } = i18n;
  const { messageHeader, message, requiredActions, skipLink, pageRedirectUri, actionUri, client, properties } = kcContext;

  const summary = [
    message.summary?.trim(),
    requiredActions?.length
      ? `<b>${requiredActions.map((action) => advancedMsgStr(`requiredAction.${action}`)).join(", ")}</b>`
      : null,
  ]
    .filter(Boolean)
    .join(" ");

  const primary = kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass");
  const next = (() => {
    if (skipLink) return null;
    if (pageRedirectUri) return { href: pageRedirectUri, label: msg("backToApplication") };
    if (actionUri) return { href: actionUri, label: msg("proceedWithAction") };
    if (client.baseUrl) return { href: client.baseUrl, label: msg("backToApplication") };
    return { href: `${properties.GEN9_UI_URL}/chat`, label: msg("continueToGen9") };
  })();

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={false}
      headerNode={<span dangerouslySetInnerHTML={{ __html: kcSanitize(messageHeader ? advancedMsgStr(messageHeader) : message.summary) }} />}
    >
      <div id="kc-info-message" className="grid gap-6">
        <p className="instruction" dangerouslySetInnerHTML={{ __html: kcSanitize(summary) }} />
        {next && (
          <a href={next.href} className={primary}>
            {next.label}
          </a>
        )}
      </div>
    </Template>
  );
}
