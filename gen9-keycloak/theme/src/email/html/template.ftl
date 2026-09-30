<#--
  This file has been claimed for ownership from @keycloakify/email-native version 260007.0.0.
  To relinquish ownership and restore this file to its original content, run the following command:
  
  $ npx keycloakify own --path "email/html/template.ftl" --revert
-->

<#-- Gen9 email layout: table-based and inline-styled (what email clients render reliably).
     Tokens from gen9-design: canvas #F6F7F9, surface #FFFFFF, ink #151722, graphite #5A5F70,
     hairline #E3E5EB, lapis #3446E0.
     The mark is set in type, ink brackets round a lapis 9: Gmail shows neither an inline <svg>
     nor a data: image (caniemail.com, html-svg and image-base64), and a linked image needs an
     address the reader's mail client can reach, which a Gen9 on localhost doesn't have. -->
<#macro emailLayout>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<style>
  a { color: #3446E0; }
  .gen9-body a[href] { display: inline-block; margin: 8px 0 4px; padding: 12px 22px; border-radius: 999px;
    background: #151722; color: #FFFFFF !important; font-weight: 600; text-decoration: none; }
  .gen9-body p { margin: 0 0 14px; }
</style>
</head>
<body style="margin:0;padding:0;background:#F6F7F9;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#F6F7F9;">
  <tr><td align="center" style="padding:40px 16px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
      style="max-width:480px;background:#FFFFFF;border:1px solid #E3E5EB;border-radius:20px;">
      <tr><td style="padding:32px 32px 4px 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
        <span aria-hidden="true" style="font-size:24px;font-weight:700;letter-spacing:0.5px;color:#151722;">[<span
          style="color:#3446E0;">9</span>]</span><span
          style="font-size:24px;font-weight:600;letter-spacing:-0.4px;color:#151722;padding-left:8px;">gen9</span>
      </td></tr>
      <tr><td class="gen9-body" style="padding:20px 32px 32px 32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:24px;color:#151722;">
        <#nested>
      </td></tr>
    </table>
    <p style="max-width:480px;margin:20px auto 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:12px;line-height:18px;color:#5A5F70;">
      You’re receiving this because of activity on your Gen9 account.
    </p>
  </td></tr>
</table>
</body>
</html>
</#macro>
