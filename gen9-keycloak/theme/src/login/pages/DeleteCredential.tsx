import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

// Keycloak sends the credential's type when it has no name ("otp"): say what it is instead
const UNNAMED: Record<string, string> = {
  otp: "your authenticator app",
  "webauthn-passwordless": "this passkey",
  webauthn: "this security key",
};

/** Confirm removing a passkey or authenticator app (delete-credential.ftl, kc_action=delete_credential:<id>). */
export default function DeleteCredential(props: PageProps<Extract<KcContext, { pageId: "delete-credential.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { url, credentialLabel } = kcContext;
  const { msg, msgStr } = i18n;
  const name = UNNAMED[credentialLabel] ?? `“${credentialLabel}”`;

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      displayMessage={false}
      headerNode={msg("deleteCredentialTitle", name)}
    >
      <p id="kc-delete-text" className="text-[0.9375rem] leading-relaxed text-muted-foreground">
        {msg("deleteCredentialMessage")}
      </p>
      <form className="mt-6 grid gap-3" action={url.loginAction} method="POST">
        <button id="kc-accept" name="accept" type="submit" className={kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass")}>
          {msgStr("doConfirmDelete")}
        </button>
        <button id="kc-decline" name="cancel-aia" value="true" type="submit" className={kcClsx("kcButtonClass", "kcButtonDefaultClass", "kcButtonBlockClass")}>
          {msgStr("doCancel")}
        </button>
      </form>
    </Template>
  );
}
