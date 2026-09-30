import { kcSanitize } from "keycloakify/lib/kcSanitize";
import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Error (error.ftl): Keycloakify's page, with a way back to Gen9 whatever the client (Temporal's web
 * UI has no home URL, so its refusal was a dead end), and Temporal's refusal under its own heading
 * rather than "Something went wrong": nothing did, it is for admins (manual-e2e.md, P3-D6).
 */
export default function Error(props: PageProps<Extract<KcContext, { pageId: "error.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { msg, msgStr } = i18n;
  const { message, client, skipLink, properties } = kcContext;
  const refused = message.summary === msgStr("gen9TemporalAdminsOnly");

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={false}
      headerNode={msg(refused ? "gen9TemporalAdminsOnlyTitle" : "errorTitle")}
    >
      <div id="kc-error-message" className="grid gap-6">
        <p className="instruction" dangerouslySetInnerHTML={{ __html: kcSanitize(message.summary) }} />
        {!skipLink && (
          <a
            id="backToApplication"
            href={client?.baseUrl || `${properties.GEN9_UI_URL}/chat`}
            className={kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass")}
          >
            {msg("backToApplication")}
          </a>
        )}
      </div>
    </Template>
  );
}
