import DefaultPage from "keycloakify/login/DefaultPage";
import { lazy, Suspense } from "react";

import { gen9Classes } from "./classes";
import { useI18n } from "./i18n";
import type { KcContext } from "./KcContext";
import Template from "./Template";

const UserProfileFormFields = lazy(() => import("keycloakify/login/UserProfileFormFields"));
const Login = lazy(() => import("./pages/Login"));
const LoginResetPassword = lazy(() => import("./pages/LoginResetPassword"));
const Info = lazy(() => import("./pages/Info"));
const LoginOtp = lazy(() => import("./pages/LoginOtp"));
const WebauthnRegister = lazy(() => import("./pages/WebauthnRegister"));
const DeleteCredential = lazy(() => import("./pages/DeleteCredential"));
const LoginRecoveryAuthnCodeConfig = lazy(() => import("./pages/LoginRecoveryAuthnCodeConfig"));
const LoginRecoveryAuthnCodeInput = lazy(() => import("./pages/LoginRecoveryAuthnCodeInput"));
const LoginOauthGrant = lazy(() => import("./pages/LoginOauthGrant"));
const LoginVerifyEmail = lazy(() => import("./pages/LoginVerifyEmail"));
const LoginUpdatePassword = lazy(() => import("./pages/LoginUpdatePassword"));
const Error = lazy(() => import("./pages/Error"));

// Ask for the password twice on registration and password changes
const doMakeUserConfirmPassword = true;

/**
 * Every Keycloak login page renders here. Sign-in, password reset, two-factor code, recovery codes,
 * passkey setup, removing a sign-in method, app consent, choosing a password and errors have
 * Gen9-specific layouts; all other pages use Keycloakify's well-tested page logic with Gen9's Template and class
 * map.
 */
export default function KcPage(props: { kcContext: KcContext }) {
  const { kcContext } = props;
  const { i18n } = useI18n({ kcContext });

  return (
    <Suspense>
      {(() => {
        switch (kcContext.pageId) {
          case "login.ftl":
            return <Login kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />;
          case "login-reset-password.ftl":
            return (
              <LoginResetPassword kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />
            );
          case "login-otp.ftl":
            return <LoginOtp kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />;
          case "webauthn-register.ftl":
            return <WebauthnRegister kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />;
          case "delete-credential.ftl":
            return <DeleteCredential kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />;
          case "login-recovery-authn-code-config.ftl":
            return (
              <LoginRecoveryAuthnCodeConfig kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />
            );
          case "login-recovery-authn-code-input.ftl":
            return (
              <LoginRecoveryAuthnCodeInput kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />
            );
          case "login-oauth-grant.ftl":
            return <LoginOauthGrant kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />;
          case "login-verify-email.ftl":
            return <LoginVerifyEmail kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />;
          case "login-update-password.ftl":
            return <LoginUpdatePassword kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />;
          case "error.ftl":
            return <Error kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />;
          case "info.ftl":
            return <Info kcContext={kcContext} i18n={i18n} classes={gen9Classes} Template={Template} doUseDefaultCss={false} />;
          default:
            return (
              <DefaultPage
                kcContext={kcContext}
                i18n={i18n}
                classes={gen9Classes}
                Template={Template}
                doUseDefaultCss={false}
                UserProfileFormFields={UserProfileFormFields}
                doMakeUserConfirmPassword={doMakeUserConfirmPassword}
              />
            );
        }
      })()}
    </Suspense>
  );
}
