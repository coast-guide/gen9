import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import { type CSSProperties, useState } from "react";

import type { I18n } from "../i18n";
import type { KcContext } from "../KcContext";

/**
 * Save your recovery codes (login-recovery-authn-code-config.ftl). Same form fields as Keycloak
 * expects; Keycloak rejects the post if the codes or their timestamp were changed. Codes are
 * numbered everywhere (page, copy, download, print) because Keycloak asks for them in order.
 */
export default function LoginRecoveryAuthnCodeConfig(
  props: PageProps<Extract<KcContext, { pageId: "login-recovery-authn-code-config.ftl" }>, I18n>,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { recoveryAuthnCodesConfigBean: codes, isAppInitiatedAction, url } = kcContext;
  const { msg, msgStr } = i18n;
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);

  const numbered = codes.generatedRecoveryAuthnCodesList.map((code, i) => `${i + 1}. ${code}`);
  const fileText = () =>
    [
      msgStr("recovery-codes-download-file-header"),
      "",
      ...numbered,
      "",
      msgStr("recovery-codes-download-file-description"),
      `${msgStr("recovery-codes-download-file-date")} ${new Date(codes.generatedAt).toLocaleString()}`,
    ].join("\n");

  const copy = async () => {
    await navigator.clipboard.writeText(numbered.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  const download = () => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([fileText()], { type: "text/plain;charset=utf-8" }));
    link.download = "gen9-recovery-codes.txt";
    link.click();
    URL.revokeObjectURL(link.href);
  };
  const print = () => {
    const w = window.open("", "_blank", "width=480,height=640");
    if (!w) return;
    const pre = w.document.createElement("pre");
    pre.style.cssText = "font: 14px/1.7 ui-monospace, monospace; margin: 48px";
    pre.textContent = fileText();
    w.document.title = "Gen9 recovery codes";
    w.document.body.append(pre);
    w.print();
    w.close();
  };

  return (
    <Template kcContext={kcContext} i18n={i18n} doUseDefaultCss={doUseDefaultCss} classes={classes} headerNode={msg("recovery-code-config-header")}>
      <div className="grid gap-5">
        <div className="grid gap-1.5" role="note">
          <p className="text-[0.9375rem] font-medium">{msg("recovery-code-config-warning-title")}</p>
          <p className="text-sm leading-relaxed text-muted-foreground">{msg("recovery-code-config-warning-message")}</p>
        </div>

        {/* One column on the narrowest phones; from 375 px two, numbered down each column (1–6, 7–12) */}
        <ol
          id="kc-recovery-codes-list"
          className="grid gap-x-4 gap-y-2 rounded-2xl bg-muted p-4 font-mono text-sm tracking-wide select-all min-[375px]:grid-flow-col min-[375px]:grid-cols-2 min-[375px]:grid-rows-[repeat(var(--rows),auto)] sm:gap-x-6 sm:px-5 sm:text-[0.9375rem]"
          style={{ "--rows": Math.ceil(codes.generatedRecoveryAuthnCodesList.length / 2) } as CSSProperties}
        >
          {codes.generatedRecoveryAuthnCodesList.map((code, i) => (
            <li key={i} className="flex gap-2.5">
              <span className="w-[2ch] shrink-0 text-right text-muted-foreground select-none">{i + 1}</span>
              <span>{code}</span>
            </li>
          ))}
        </ol>

        <div className="grid grid-cols-3 gap-2">
          <button id="copyRecoveryCodes" type="button" onClick={copy} className={kcClsx("kcButtonClass", "kcButtonDefaultClass")} aria-live="polite">
            {copied ? msgStr("recovery-codes-copied") : msgStr("recovery-codes-copy")}
          </button>
          <button id="downloadRecoveryCodes" type="button" onClick={download} className={kcClsx("kcButtonClass", "kcButtonDefaultClass")}>
            {msgStr("recovery-codes-download")}
          </button>
          <button id="printRecoveryCodes" type="button" onClick={print} className={kcClsx("kcButtonClass", "kcButtonDefaultClass")}>
            {msgStr("recovery-codes-print")}
          </button>
        </div>

        <form action={url.loginAction} id="kc-recovery-codes-settings-form" method="post" className="grid gap-5">
          <input type="hidden" name="generatedRecoveryAuthnCodes" value={codes.generatedRecoveryAuthnCodesAsString} />
          <input type="hidden" name="generatedAt" value={codes.generatedAt} />
          <input type="hidden" id="userLabel" name="userLabel" value={msgStr("recovery-codes-label-default")} />

          <div className="grid gap-3">
            <label>
              <input
                type="checkbox"
                id="kcRecoveryCodesConfirmationCheck"
                name="kcRecoveryCodesConfirmationCheck"
                checked={saved}
                onChange={(event) => setSaved(event.target.checked)}
              />
              {msg("recovery-codes-confirmation-message")}
            </label>
            <label>
              <input type="checkbox" id="logout-sessions" name="logout-sessions" value="on" />
              {msg("logoutOtherSessions")}
            </label>
          </div>

          <div className="grid gap-3">
            <button
              id="saveRecoveryAuthnCodesBtn"
              type="submit"
              disabled={!saved}
              className={kcClsx("kcButtonClass", "kcButtonPrimaryClass", "kcButtonBlockClass")}
            >
              {msgStr("recovery-codes-action-complete")}
            </button>
            {isAppInitiatedAction && (
              <button
                id="cancelRecoveryAuthnCodesBtn"
                type="submit"
                name="cancel-aia"
                value="true"
                className={kcClsx("kcButtonClass", "kcButtonDefaultClass", "kcButtonBlockClass")}
              >
                {msg("recovery-codes-action-cancel")}
              </button>
            )}
          </div>
        </form>
      </div>
    </Template>
  );
}
