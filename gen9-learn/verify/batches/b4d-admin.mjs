// Batch 6, deeper: admin rights, after b4. Ada (signed in since b3) makes the person an admin, and the
// person, signed in again, sees the admin pages; Ada takes it back while they're signed in, and the
// next admin page they open refuses them at once, although their token still says gen9-admin. Then
// the record: Gen9's audit log names Ada as the actor, where Keycloak's own admin events name
// gen9-agent's service account; Ada reads it on the Audit log page. No model call.
import { APP, appdb, check, kcdb, kcEnv, navigation, nextWindow, otpPolicy, signInWithPassword, totp } from "../lib.mjs";

const menu = async (page, email, label) => {
  await page.click(`button[aria-label="Actions for ${email}"]`);
  await page.waitForSelector("[role=menuitem]");
  for (const item of await page.$$("[role=menuitem]")) if ((await item.evaluate((el) => el.textContent.trim())) === `${label}…`) await item.click();
  await page.waitForSelector("[role=alertdialog]");
  const asked = await page.$eval("[role=alertdialog]", (d) => d.innerText.replace(/\s+/g, " ").slice(0, 200));
  for (const button of await page.$$("[role=alertdialog] button")) if ((await button.evaluate((el) => el.textContent.trim())) === label) await button.click();
  await page.waitForNetworkIdle({ idleTime: 500 });
  return asked;
};
const adminLinks = (page) => page.$$eval("nav a", (as) => as.map((a) => a.textContent.trim()).filter((t) => ["Users", "Plugins", "Audit log"].includes(t)));
const groupsOf = (sub) => kcdb(`select coalesce(string_agg(g.name, ',' order by g.name), '') from user_group_membership m join keycloak_group g on g.id = m.group_id where m.user_id = '${sub}'`);

export default async function admin(ctx) {
  const { page, user } = ctx;
  const obs = {};
  const policy = await otpPolicy();
  const ada = await ctx.adminContext.newPage();
  const since = new Date().toISOString();

  // Ada makes the person an admin
  await ada.goto(`${APP}/admin/users`, { waitUntil: "networkidle0" });
  obs.makeAsked = await menu(ada, user.email, "Make admin");
  obs.groupsAfterMake = groupsOf(user.sub);
  check(obs.groupsAfterMake.split(",").includes("admins"), "Make admin puts the person in Keycloak's admins group, which holds gen9-admin", `${obs.makeAsked}; groups: ${obs.groupsAfterMake}`);

  // The person signs in: their token has the role, the admin pages are theirs
  await signInWithPassword(page, user.email, user.password);
  user.otpCounter = await nextWindow(policy, user.otpCounter);
  await page.type("#otp", totp(user.totpSecret, policy, user.otpCounter));
  await navigation(page, page.click("#kc-login"));
  await page.goto(`${APP}/admin/users`, { waitUntil: "networkidle0" });
  obs.linksAsAdmin = await adminLinks(page);
  obs.usersPage = await page.$eval("main h1", (h) => h.textContent.trim()).catch(() => null);
  check(obs.linksAsAdmin.length === 3 && obs.usersPage === "Users", "signed in again, the person has the admin links and the Users page", obs.linksAsAdmin.join(", "));

  // Ada takes it back; the person's token still says gen9-admin, but the next page refuses them
  await ada.goto(`${APP}/admin/users`, { waitUntil: "networkidle0" });
  obs.removeAsked = await menu(ada, user.email, "Remove admin access");
  obs.groupsAfterRemove = groupsOf(user.sub);
  await page.goto(`${APP}/admin/audit`, { waitUntil: "networkidle0" });
  obs.refused = await page.$eval("main", (m) => m.innerText.replace(/\s+/g, " ").slice(0, 160));
  obs.linksAfter = await adminLinks(page);
  check(
    !obs.groupsAfterRemove.split(",").includes("admins") && /You need admin access/.test(obs.refused) && obs.linksAfter.length === 0,
    "removed while signed in: the next admin page refuses them at once, and the links are gone, though their token was issued with the role",
    `${obs.removeAsked}; ${obs.refused}`,
  );

  // The record: Gen9's names Ada; Keycloak's names gen9-agent's service account
  const adaSub = kcdb(`select id from user_entity where email = '${kcEnv.GEN9_SEED_ADMIN_EMAIL}' and realm_id = (select id from realm where name = 'gen9')`);
  obs.audit = appdb(
    `select string_agg(action || ' ' || outcome || ' ' || coalesce(detail::text, '') || ' by ' || case when actor = '${adaSub}' then 'Ada' else 'someone else' end, '; ' order by id) from audit_events where target = '${user.sub}' and at >= '${since}'`,
  );
  obs.keycloakAdminEvents = kcdb(
    `select string_agg(a.operation_type || ' ' || replace(a.resource_path, '${user.sub}', '<sub>') || ' by ' || c.client_id, '; ' order by a.admin_event_time) from admin_event_entity a left join client c on c.id = a.auth_client_id where a.resource_path like '%${user.sub}%' and a.admin_event_time >= ${Date.parse(since)}`,
  );
  check(
    /admin\.user\.update success \{"admin": true\} by Ada/.test(obs.audit) && /admin\.user\.update success \{"admin": false\} by Ada/.test(obs.audit) && /by gen9-agent/.test(obs.keycloakAdminEvents),
    "Gen9's audit log records both changes with Ada as the actor; Keycloak's admin events name gen9-agent's service account",
    `${obs.audit} | Keycloak: ${obs.keycloakAdminEvents}`,
  );

  // Ada reads it on the Audit log page
  await ada.goto(`${APP}/admin/audit`, { waitUntil: "networkidle0" });
  obs.auditPage = await ada.$$eval('ol[aria-label="Events, newest first"] > li', (rows, email) => rows.map((r) => r.innerText.replace(/\s+/g, " ").trim()).filter((t) => t.includes(email)).slice(0, 5), user.email);
  obs.auditFilters = await ada.$$eval('nav[aria-label="Show"] a, nav[aria-label="Show"] button', (as) => as.map((a) => a.textContent.trim()));
  check(
    obs.auditPage.some((t) => t.includes(`Removed admin access from ${user.email}`)) && obs.auditPage.some((t) => t.includes(`Made ${user.email} an admin`)),
    "Ada reads both on the Audit log page, in plain words",
    `${obs.auditFilters.join(", ")}; ${obs.auditPage.join(" | ").replaceAll(user.email, "<email>").slice(0, 240)}`,
  );
  await ada.close();
  return obs;
}
