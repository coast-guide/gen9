# Production image: Next.js standalone output served by `node server.js`.
# Follows the official Next.js with-docker example (v16.3.6) and Docker's Next.js guide,
# trimmed to npm (this project's package manager).

# Node.js Active LTS, pinned as tag@digest: the tag is for humans, the digest is what Docker pulls.
# To upgrade: docker buildx imagetools inspect node:<version>-trixie-slim
ARG NODE_IMAGE=node:24.21.0-trixie-slim@sha256:8ec5d7557396cfe32d21c3f9c13072355ceab22b584578ca4bb28af31120cffe

# ============================================
# Stage 1: install dependencies
# ============================================
FROM ${NODE_IMAGE} AS dependencies

WORKDIR /app

# Copy only the manifests first so this layer is cached until dependencies change
COPY package.json package-lock.json ./

RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

# ============================================
# Stage 2: build the standalone output
# ============================================
FROM ${NODE_IMAGE} AS builder

WORKDIR /app

COPY --from=dependencies /app/node_modules ./node_modules
COPY . .

ENV NODE_ENV=production
# Opt out of anonymous telemetry: https://nextjs.org/telemetry
# ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# ============================================
# Stage 3: runtime
# ============================================
FROM ${NODE_IMAGE} AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000
# Docker sets HOSTNAME to the container ID; server.js binds to it, so listen on all interfaces instead
ENV HOSTNAME="0.0.0.0"
# ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=builder --chown=node:node /app/public ./public

# The server writes its prerender and image caches under .next at runtime. The server needs only
# `node`: the npm and corepack CLIs that come with the base image go (less to patch; Docker Scout
# flagged npm's own dependencies)
RUN mkdir .next && chown node:node .next \
  && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
     /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack

# Only the files traced by `next build`, plus static assets (standalone does not copy them)
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
# The MCP Apps sandbox proxy, run by the `sandbox` service from this image (sandbox/server.ts)
COPY --chown=node:node sandbox/server.ts sandbox/package.json ./sandbox/

USER node

EXPOSE 3000

CMD ["node", "server.js"]
