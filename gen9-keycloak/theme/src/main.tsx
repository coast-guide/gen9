import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./index.css";
import { KcPage } from "./kc.gen";

// `npm run dev` previews a page with mock data: /?page=register.ftl (dev only, not in the jar)
if (import.meta.env.DEV && !window.kcContext) {
  const { getKcContextMock } = await import("./login/mocks");
  const pageId = (new URLSearchParams(location.search).get("page") ?? "login.ftl") as Parameters<typeof getKcContextMock>[0]["pageId"];
  window.kcContext = getKcContextMock({ pageId, overrides: {} });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>{window.kcContext ? <KcPage kcContext={window.kcContext} /> : <h1>No Keycloak context</h1>}</StrictMode>,
);
