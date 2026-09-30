import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Consent (login-oauth-grant.ftl), shown when an app that requires it (Gen9 CLI) asks for access.
 * Same form as Keycloak expects (code, accept/cancel). It names the app and warns against
 * approving a sign-in you didn't start: that is how device-code phishing works (RFC 8628 §5.4).
 */
export default function LoginOauthGrant(props: PageProps<Extract<KcContext, { pageId: "login-oauth-grant.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { url, oauth, client } = kcContext;
  const { msg, msgStr, advancedMsg, advancedMsgStr } = i18n;
  const appName = client.name ? advancedMsgStr(client.name) : client.clientId;
  // What the app is for first: Gen9's own scopes carry their words (configure.sh's bound_scope), Keycloak's
  // standard ones a message key ("${profileScopeConsentText}"), which follow; otherwise Keycloak's order (P2-K3)
  const scopes = [...oauth.clientScopesRequested].sort(
    (a, b) => Number(a.consentScreenText.startsWith("${")) - Number(b.consentScreenText.startsWith("${")),
  );

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      headerNode={msg("oauthGrantTitle", appName)}
    >
      <div id="kc-oauth" className="grid gap-5">
        <div className="grid gap-2">
          <p className="text-[0.9375rem] font-medium">{msg("oauthGrantRequest")}</p>
          <ul className="grid gap-1.5 rounded-2xl bg-muted px-5 py-4 text-sm">
            {scopes.map((scope) => (
              <li key={scope.consentScreenText} className="flex items-center gap-2.5">
                <span className="size-1.5 shrink-0 rounded-full bg-muted-foreground" aria-hidden />
                <span>
                  {advancedMsg(scope.consentScreenText)}
                  {scope.dynamicScopeParameter && <>: {scope.dynamicScopeParameter}</>}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <p role="note" className="text-sm leading-relaxed text-muted-foreground">
          {msg("oauthGrantOnlyIfYou", appName)}
        </p>

        <form className="grid gap-3" action={url.oauthAction} method="POST">
          <input type="hidden" name="code" value={oauth.code} />
          <button id="kc-login" name="accept" type="submit" className={kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass")}>
            {msgStr("oauthGrantAllow")}
          </button>
          <button id="kc-cancel" name="cancel" type="submit" className={kcClsx("kcButtonClass", "kcButtonDefaultClass", "kcButtonBlockClass")}>
            {msgStr("oauthGrantDeny")}
          </button>
        </form>
      </div>
    </Template>
  );
}
