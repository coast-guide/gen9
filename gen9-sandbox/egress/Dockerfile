# syntax=docker/dockerfile:1.7
# OpenSandbox's egress sidecar (Apache-2.0), pinned by digest, with a baseline no sandbox's policy
# can lift: deny.always. To upgrade: this digest, the source commit below and config.toml's
# [egress] image together.

# Its two Go binaries, rebuilt from the same release's source with a current Go: OpenSandbox builds
# them with Go 1.25.9, in whose standard library govulncheck finds 14 vulnerabilities the egress
# binary reaches (docs/plans/manual-e2e.md, P6-D1c5). The commit pins the source; Go checks each
# module against go.sum. The flags are upstream's (components/egress/Dockerfile).
FROM golang:1.25.14-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS builder
ARG COMMIT=b1a29cf93a823a95913f7943010febb3f29de05c
ADD https://github.com/opensandbox-group/OpenSandbox.git#${COMMIT} /src
ENV CGO_ENABLED=0
RUN version="-X github.com/alibaba/opensandbox/internal/version.Version=release-1.1.0 \
              -X github.com/alibaba/opensandbox/internal/version.GitCommit=${COMMIT}" \
    && cd /src/components/egress \
    && go build -trimpath -buildvcs=false -ldflags "-buildid= -B none $version" -o /out/egress . \
    && cd /src/components/internal \
    && go build -trimpath -buildvcs=false -ldflags "-buildid= -B none $version" \
       -o /out/opensandbox-supervisor ./cmd/supervisor

FROM opensandbox/egress:release-1.1.0@sha256:56429c89b7175c2a24af62ca93a99776f03ea67a0beb74d63c8ddb34ff16fd97
COPY --from=builder /out/egress /opt/opensandbox-egress/egress
COPY --from=builder /out/opensandbox-supervisor /opt/opensandbox-egress/supervisor
# Debian's security updates since OpenSandbox built it (its OpenSSL: DLA-4795-1)
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
COPY deny.always /var/egress/rules/deny.always
# A secret goes only to a TLS peer its binding trusts, not to wherever the Host header points:
# OpenSandbox #1758, its fix PR #1759 carried until a release has it (sni-binding.py). The check
# fails the build if system.py isn't release-1.1.0's
COPY sni-binding.py /tmp/sni-binding.py
RUN echo "b8930ed48d1903413cdc3b7d0bf49dac7a8bff4f6c1a470868881eb38e215413  /var/egress/mitmscripts/system.py" | sha256sum -c - \
    && python3 /tmp/sni-binding.py /var/egress/mitmscripts/system.py \
    && rm /tmp/sni-binding.py
# Each change to the sandbox's rules is written here too, and a restarted egress process reads it
# before the rules the sandbox was created with: without it, egress restarted after a crash reopened
# the hosts gen9-agent had closed since (manual-e2e.md, P4-E4). OpenSandbox's own setting (#525)
ENV OPENSANDBOX_EGRESS_POLICY_FILE=/var/egress/policy.json
