#!/usr/bin/env bash
# Copy the design system into the apps that use it. Copies are committed (each stack builds on
# its own, without reaching outside its folder) and must never be edited by hand.
#   ./sync.sh          copy
#   ./sync.sh --check  exit 1 if any copy differs from its source (use in CI / before commit)
set -euo pipefail
cd "$(dirname "$0")"
ROOT=..

# source                                  destination
MAP="
theme.css                                 gen9-ui/app/gen9-theme.css
fonts/InstrumentSans-Variable.woff2       gen9-ui/app/fonts/InstrumentSans-Variable.woff2
brand/app-icon.svg                        gen9-ui/app/icon.svg
brand/favicon.ico                         gen9-ui/app/favicon.ico
brand/png/apple-touch-icon.png            gen9-ui/app/apple-icon.png
brand/png/icon-192.png                    gen9-ui/public/brand/icon-192.png
brand/png/icon-512.png                    gen9-ui/public/brand/icon-512.png
brand/png/icon-maskable-512.png           gen9-ui/public/brand/icon-maskable-512.png
brand/mark.svg                            gen9-ui/public/brand/mark.svg
brand/mark-on-dark.svg                    gen9-ui/public/brand/mark-on-dark.svg
brand/wordmark.svg                        gen9-ui/public/brand/wordmark.svg
brand/wordmark-on-dark.svg                gen9-ui/public/brand/wordmark-on-dark.svg
react/logo.tsx                            gen9-ui/components/brand/logo.tsx
react/logo.tsx                            gen9-keycloak/theme/src/components/logo.tsx
theme.css                                 gen9-keycloak/theme/src/gen9-theme.css
fonts/InstrumentSans-Variable.woff2       gen9-keycloak/theme/src/assets/InstrumentSans-Variable.woff2
brand/app-icon.svg                        gen9-keycloak/theme/public/favicon.svg
brand/mark.svg                            gen9-keycloak/theme/src/assets/mark.svg
brand/wordmark.svg                        gen9-keycloak/theme/src/assets/wordmark.svg
brand/wordmark-on-dark.svg                gen9-keycloak/theme/src/assets/wordmark-on-dark.svg
"

CHECK=false
[ "${1:-}" = "--check" ] && CHECK=true
status=0
while read -r src dst; do
  [ -n "$src" ] || continue
  # Skip destinations whose app folder does not exist yet
  app_dir="$ROOT/${dst%%/*}"
  [ -d "$app_dir" ] || continue
  case "$dst" in gen9-keycloak/theme/*) [ -d "$ROOT/gen9-keycloak/theme" ] || continue ;; esac
  if $CHECK; then
    if ! cmp -s "$src" "$ROOT/$dst"; then echo "out of sync: $dst (source: gen9-design/$src)"; status=1; fi
  else
    mkdir -p "$(dirname "$ROOT/$dst")"
    cp "$src" "$ROOT/$dst"
    echo "synced $dst"
  fi
done <<<"$MAP"
$CHECK && [ $status -eq 0 ] && echo "design system copies are in sync"
exit $status
