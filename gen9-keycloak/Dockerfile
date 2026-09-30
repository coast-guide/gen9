# syntax=docker/dockerfile:1
# Keycloak, production mode, as an optimized image (build options baked in by `kc.sh build`,
# so `start --optimized` skips the build step at every boot). Follows Keycloak's container guide:
# https://www.keycloak.org/server/containers

# Pinned as tag@digest. To upgrade: docker buildx imagetools inspect quay.io/keycloak/keycloak:<version>
ARG KEYCLOAK_IMAGE=quay.io/keycloak/keycloak:26.7.4@sha256:82a77884f3af238beab1e7afd63b5f530e1b5c0590bd7aa60b40a40463e29b2c
ARG NODE_IMAGE=node:24.21.0-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6

# ============================================
# Gen9 login theme (theme/): Keycloakify builds the React app into a theme jar (needs Maven)
# ============================================
FROM ${NODE_IMAGE} AS theme
RUN apt-get update && apt-get install -y --no-install-recommends maven && rm -rf /var/lib/apt/lists/*
WORKDIR /theme
COPY theme/package.json theme/package-lock.json ./
# Scripts off here: the postinstall (keycloakify sync-extensions) needs the sources, copied next
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund --ignore-scripts
COPY theme/ ./
RUN npx keycloakify sync-extensions
RUN --mount=type=cache,target=/root/.m2 npm run build-keycloak-theme

# ============================================
# Keycloak: optimized build with the theme and the password blocklist
# ============================================
FROM ${KEYCLOAK_IMAGE} AS builder

# Build-time options: changing any of these needs a rebuild (docker compose build)
ENV KC_DB=postgres
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=true
# OAuth Client ID Metadata Documents, for MCP clients that register themselves with Gen9's MCP
# server (experimental since 26.6; Keycloak's "Integrating with Model Context Protocol")
ENV KC_FEATURES=cimd

WORKDIR /opt/keycloak

# Blocklist for the realm's passwordBlacklist policy (NIST SP 800-63B-4 requires one): the UK NCSC
# 100k most-used passwords, from SecLists pinned to a commit and verified by checksum.
ADD --chown=keycloak:keycloak --checksum=sha256:c2e5696882c603b76bb67a47ee970897e5a76fc4c3f5547abe3d0ca340c576e0 \
    https://raw.githubusercontent.com/danielmiessler/SecLists/1a7bb9127eca9e6ff2fc0301c597fe6e16a0cb56/Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt \
    /opt/keycloak/data/password-blacklists/ncsc-100k.txt

# Themes are providers: they must be in place before `kc.sh build`
COPY --from=theme /theme/dist_keycloak/gen9-keycloak-theme.jar /opt/keycloak/providers/

RUN /opt/keycloak/bin/kc.sh build

FROM ${KEYCLOAK_IMAGE}
COPY --from=builder /opt/keycloak/ /opt/keycloak/

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized", "--import-realm"]
