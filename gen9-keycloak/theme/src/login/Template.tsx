import { kcSanitize } from "keycloakify/lib/kcSanitize";
import type { TemplateProps } from "keycloakify/login/TemplateProps";
import { useInitialize } from "keycloakify/login/Template.useInitialize";
import { getKcClsx } from "keycloakify/login/lib/kcClsx";
import { useEffect } from "react";

import { Wordmark } from "../components/logo";
import type { I18n } from "./i18n";
import type { KcContext } from "./KcContext";

const ALERT_TONE = {
  error: "border-destructive/30 bg-destructive/8 text-destructive",
  warning: "border-leaf/40 bg-leaf/10 text-foreground",
  success: "border-success/30 bg-success/8 text-foreground",
  info: "border-border bg-accent text-foreground",
} as const;

const ALERT_ICON = {
  error: "kcFeedbackErrorIcon",
  warning: "kcFeedbackWarningIcon",
  success: "kcFeedbackSuccessIcon",
  info: "kcFeedbackInfoIcon",
} as const;

/** Keycloak pages follow the device's light/dark setting, like gen9-ui's default. */
function useSystemColorScheme() {
  useEffect(() => {
    const query = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => document.documentElement.classList.toggle("dark", query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);
}

/**
 * The frame of every Keycloak page in Gen9: brand, one column, the page title, feedback,
 * the page's form, then secondary paths. Mobile first: edge to edge on phones, a sheet on larger
 * screens. Logic mirrors Keycloakify's default Template (messages, restart login, try another
 * way, Keycloak's session scripts); only the presentation is Gen9's.
 */
export default function Template(props: TemplateProps<KcContext, I18n>) {
  const {
    displayInfo = false,
    displayMessage = true,
    displayRequiredFields = false,
    headerNode,
    socialProvidersNode = null,
    infoNode = null,
    documentTitle,
    kcContext,
    i18n,
    doUseDefaultCss,
    classes,
    children,
  } = props;

  const { kcClsx } = getKcClsx({ doUseDefaultCss, classes });
  const { msg, msgStr } = i18n;
  const { auth, url, message, isAppInitiatedAction } = kcContext;

  // Each page is titled by its own heading (WCAG 2.4.2: a tab or a screen reader tells "Reset your
  // password" from "Sign in"), after every render since the heading comes with the page
  useEffect(() => {
    const heading = document.getElementById("kc-page-title")?.textContent?.trim();
    document.title = documentTitle ?? (heading ? (heading.includes("Gen9") ? heading : `${heading} – Gen9`) : msgStr("loginTitle"));
  });
  useSystemColorScheme();

  // Loads Keycloak's session scripts (multi-tab login detection); no Keycloak CSS (doUseDefaultCss=false)
  const { isReadyToRender } = useInitialize({ kcContext, doUseDefaultCss });
  if (!isReadyToRender) return null;

  const showUsername = auth !== undefined && auth.showUsername && !auth.showResetCredentials;
  // gen9-ui's privacy page (GDPR Art. 13), from every sign-in page and first of all at sign-up
  const privacy = `${kcContext.properties.GEN9_UI_URL.replace(/\/$/, "")}/privacy`;
  const privacyLink = (
    <a href={privacy} className="font-medium text-link underline-offset-4 hover:underline">
      {msg("gen9PrivacyLink")}
    </a>
  );

  return (
    <div className="gen9-page flex min-h-dvh flex-col bg-background px-safe text-foreground">
      <header className="pt-safe">
        <div className="mx-auto flex h-16 w-full max-w-md items-center px-6 sm:max-w-6xl sm:px-8">
          <Wordmark className="h-7" />
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-md flex-1 flex-col px-6 pt-4 pb-12 sm:justify-center sm:pt-0 sm:pb-24">
        <div className="sm:rounded-3xl sm:border sm:bg-card sm:p-9 sm:shadow-[0_24px_48px_-32px_color-mix(in_oklch,var(--foreground)_28%,transparent)]">
          {showUsername ? (
            <div id="kc-username" className="mb-6 grid gap-4">
              <h1 id="kc-page-title" className="text-[1.75rem] leading-tight font-semibold tracking-[-0.02em] text-balance">
                {headerNode}
              </h1>
              <div className="flex items-center gap-3 rounded-full border bg-background py-1.5 pr-1.5 pl-4">
                <span id="kc-attempted-username" className="min-w-0 flex-1 truncate text-sm">
                  {auth.attemptedUsername}
                </span>
                <a
                  id="reset-login"
                  href={url.loginRestartFlowUrl}
                  className="rounded-full px-3 py-1.5 text-sm font-medium text-link hover:bg-accent"
                >
                  {msg("restartLoginTooltip")}
                </a>
              </div>
            </div>
          ) : (
            <h1 id="kc-page-title" className="mb-6 text-[1.75rem] leading-tight font-semibold tracking-[-0.02em] text-balance">
              {headerNode}
            </h1>
          )}

          {displayRequiredFields && (
            <p className="-mt-3 mb-5 text-sm text-muted-foreground">
              <span className="text-destructive">*</span> {msg("requiredFields")}
            </p>
          )}

          {/* App-initiated actions don't show "you need to do X" warnings: the user asked for X */}
          {displayMessage && message !== undefined && (message.type !== "warning" || !isAppInitiatedAction) && (
            <div role={message.type === "error" ? "alert" : "status"} className={`${kcClsx("kcAlertClass")} mb-6 ${ALERT_TONE[message.type]}`}>
              <span className={kcClsx(ALERT_ICON[message.type])} aria-hidden />
              <span className={kcClsx("kcAlertTitleClass")} dangerouslySetInnerHTML={{ __html: kcSanitize(message.summary) }} />
            </div>
          )}

          {children}

          {kcContext.pageId === "register.ftl" && (
            <p id="gen9-register-privacy" className="mt-6 text-sm text-muted-foreground">
              {msg("gen9RegisterPrivacy")} {privacyLink}
            </p>
          )}

          {auth !== undefined && auth.showTryAnotherWayLink && (
            <form id="kc-select-try-another-way-form" action={url.loginAction} method="post" className="mt-5 text-center">
              <input type="hidden" name="tryAnotherWay" value="on" />
              <button type="submit" className="text-sm font-medium text-link underline-offset-4 hover:underline">
                {msg("doTryAnotherWay")}
              </button>
            </form>
          )}

          {socialProvidersNode}

          {displayInfo && (
            <div id="kc-info" className="mt-8 border-t pt-6 text-center text-sm text-muted-foreground">
              {infoNode}
            </div>
          )}
        </div>
      </main>
      {/* Sign-up says it in its card, next to the form that collects the email */}
      {kcContext.pageId !== "register.ftl" && (
        <footer className="pb-safe">
          <p className="mx-auto max-w-md px-6 pb-6 text-center text-sm">{privacyLink}</p>
        </footer>
      )}
    </div>
  );
}
