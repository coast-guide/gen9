# Swagger UI, for the API's `/docs`

The page `/docs` (api_docs.py) serves these files from gen9-agent itself, so no script comes from a
CDN onto the API's origin: Swagger UI 5.33.0, from the npm package `swagger-ui-dist`, checked
against the registry's integrity before they were copied here (docs/plans/manual-e2e.md, P8-M1).

| File | sha256 |
| --- | --- |
| `swagger-ui-bundle.js` | `62df541529080464a7660adc793eab7128c6193ce3be24ddc1e0e0a4a63edc2f` |
| `swagger-ui.css` | `1ac324f7dcd27e4b9386b4bd6421271ec147e922a22c05ba24b11515e9aa6321` |

`tests/test_docs_page.py` checks them against these. Swagger UI is Apache-2.0 (`LICENSE`,
`NOTICE`, and the bundle's own notices in `swagger-ui-bundle.js.LICENSE.txt`); the repository's
NOTICE names it.

To move to a newer release, at least 7 days old (the repository's cooldown):

```bash
v=5.x.y
meta=$(curl -s https://registry.npmjs.org/swagger-ui-dist/$v)
curl -s -o s.tgz "$(echo "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dist"]["tarball"])')"
[ "sha512-$(openssl dgst -sha512 -binary s.tgz | base64 -w0)" = "$(echo "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dist"]["integrity"])')" ] && echo integrity OK
tar xzf s.tgz --strip-components=1 package/swagger-ui-bundle.js package/swagger-ui.css package/LICENSE package/NOTICE package/swagger-ui-bundle.js.LICENSE.txt
```

then the sums above and in the test.
