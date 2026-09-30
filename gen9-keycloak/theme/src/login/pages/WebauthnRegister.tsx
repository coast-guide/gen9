import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import { useScript } from "keycloakify/login/pages/WebauthnRegister.useScript";
import { useEffect, useRef, useState } from "react";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Add a passkey (webauthn-register.ftl). Keycloak's own script (webauthnRegister.js) still creates the
 * passkey and posts the form; it asks for a name with window.prompt once the device has created it.
 * Here the name is a field on the page instead, pre-filled with one unique to this device and moment:
 * Keycloak rejects a name the user already has, and the stock default ("Passkey (Default Label)")
 * made every second passkey fail after the device had already stored it.
 */
export default function WebauthnRegister(props: PageProps<Extract<KcContext, { pageId: "webauthn-register.ftl" }>, I18n>) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { url, isSetRetry, isAppInitiatedAction } = kcContext;
  const { msg, msgStr } = i18n;

  const authButtonId = "authenticateWebAuthnButton";
  useScript({ authButtonId, kcContext, i18n });

  const [defaultName] = useState(defaultPasskeyName);
  const name = useRef(defaultName);
  useEffect(() => {
    const nativePrompt = window.prompt;
    window.prompt = () => name.current.trim() || defaultName;
    return () => {
      window.prompt = nativePrompt;
    };
  }, [defaultName]);

  return (
    <Template kcContext={kcContext} i18n={i18n} doUseDefaultCss={doUseDefaultCss} classes={classes} headerNode={msg("webauthn-registration-title")}>
      <div className="grid gap-5">
        <p className="text-sm text-muted-foreground">{msg("passkeyRegisterIntro")}</p>

        <form id="register" className="grid gap-5" action={url.loginAction} method="post">
          <div className="grid gap-2">
            <label htmlFor="passkey-name" className={kcClsx("kcLabelClass")}>
              {msg("passkeyNameLabel")}
            </label>
            <input
              id="passkey-name"
              type="text"
              defaultValue={defaultName}
              maxLength={100}
              autoComplete="off"
              onChange={(event) => {
                name.current = event.target.value;
              }}
              className={kcClsx("kcInputClass")}
              aria-describedby="passkey-name-hint"
            />
            <p id="passkey-name-hint" className="text-sm text-muted-foreground">
              {msg("passkeyNameHint")}
            </p>
          </div>

          <input type="hidden" id="clientDataJSON" name="clientDataJSON" />
          <input type="hidden" id="attestationObject" name="attestationObject" />
          <input type="hidden" id="publicKeyCredentialId" name="publicKeyCredentialId" />
          <input type="hidden" id="authenticatorLabel" name="authenticatorLabel" />
          <input type="hidden" id="transports" name="transports" />
          <input type="hidden" id="authenticatorAttachment" name="authenticatorAttachment" />
          <input type="hidden" id="error" name="error" />

          <label>
            <input type="checkbox" id="logout-sessions" name="logout-sessions" value="on" />
            {msg("logoutOtherSessions")}
          </label>
        </form>

        <div className="grid gap-3">
          <button id={authButtonId} type="button" className={kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass")}>
            <span className="gen9-icon gen9-icon-key" aria-hidden />
            {msgStr("doRegisterSecurityKey")}
          </button>
          {!isSetRetry && isAppInitiatedAction && (
            <form action={url.loginAction} id="kc-webauthn-settings-form" method="post">
              <button
                type="submit"
                id="cancelWebAuthnAIA"
                name="cancel-aia"
                value="true"
                className={kcClsx("kcButtonClass", "kcButtonDefaultClass", "kcButtonBlockClass")}
              >
                {msg("doCancel")}
              </button>
            </form>
          )}
        </div>
      </div>
    </Template>
  );
}

/** "Linux, 3 Mar 2030, 09:30": where and when it was made, in the user's locale. */
function defaultPasskeyName(): string {
  const when = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date());
  return `${deviceName()}, ${when}`;
}

function deviceName(): string {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  // iPadOS reports itself as a Mac
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
  if (/Android/.test(ua)) return "Android";
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows";
  if (/CrOS/.test(ua)) return "Chromebook";
  if (/Linux/.test(ua)) return "Linux";
  return "Passkey";
}
