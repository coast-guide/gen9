# syntax=docker/dockerfile:1.7
# OpenSandbox's egress sidecar (Apache-2.0), rebuilt by Gen9 from that release, with a baseline no
# sandbox's policy can lift: deny.always. OpenSandbox builds it on Debian 12 with Go 1.25.9 and
# mitmproxy 11.0.2, which caps h11, cryptography, tornado and pyOpenSSL at versions with advisories
# (h11's is Critical), on main too; mitmproxy 12 needs Python 3.12, which Debian 12 hasn't. So this
# is upstream's image (components/egress/Dockerfile) on Debian 13, with a supported Go and
# mitmproxy 12, from a lock with hashes (requirements.txt; docs/plans/manual-e2e.md, P6-D1c5).
# mitmproxy 12.2.3 caps h2, tornado, cryptography, msgpack and pyOpenSSL below their security
# fixes too: the lock overrides those caps (overrides.txt), and the lock is installed as it is
# (--no-deps), as pip would refuse the overrides against mitmproxy's own requirements.
# To upgrade: the upstream image's digest, its release's COMMIT, the Go and Debian images, then
# the lock, its command at its head (`uv pip compile requirements.in --override overrides.txt …`).

# Its two Go binaries, from the release's commit, with a supported Go (1.25 left support when 1.27
# came out). The commit pins the source; Go checks each module against go.sum. Upstream's flags.
FROM golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d AS builder
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

# OpenSandbox's own image of that release: where its mitmproxy addon, settings and cleanup come from
FROM opensandbox/egress:release-1.1.0@sha256:56429c89b7175c2a24af62ca93a99776f03ea67a0beb74d63c8ddb34ff16fd97 AS upstream

FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
# Upstream's packages: iptables and nftables for the DNS redirect and the policy, ca-certificates
# for TLS upstream, Python for mitmproxy, and its tools for looking inside a sidecar; and Debian's
# security updates to what the pinned base has (its libpcre2 had a fixed High one)
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iptables iproute2 nftables ca-certificates sudo curl wget net-tools dnsutils netcat-openbsd \
        iputils-ping traceroute telnet tcpdump nmap htop procps strace lsof python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
# mitmdump runs as the mitmproxy user (iptables lets its own traffic out), with its settings in its
# home. Every package from the lock, each checked against its hash; pip goes once they are in
COPY requirements.txt /tmp/requirements.txt
RUN useradd -r -u 10042 -d /var/lib/mitmproxy -s /usr/sbin/nologin mitmproxy \
    && mkdir -p /var/lib/mitmproxy/.mitmproxy /var/egress \
    && chown -R mitmproxy:mitmproxy /var/lib/mitmproxy \
    && pip3 install --no-cache-dir --break-system-packages --require-hashes --no-deps -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt && mitmdump --version \
    && apt-get purge -y python3-pip && apt-get autoremove -y --purge && rm -rf /var/lib/apt/lists/*
COPY --from=upstream --chown=mitmproxy:mitmproxy /var/lib/mitmproxy/.mitmproxy/config.yaml /var/lib/mitmproxy/.mitmproxy/config.yaml
COPY --from=upstream /var/egress/mitmscripts /var/egress/mitmscripts
COPY --from=upstream /opt/opensandbox-egress/cleanup.sh /opt/opensandbox-egress/cleanup.sh
COPY --from=builder /out/egress /opt/opensandbox-egress/egress
COPY --from=builder /out/opensandbox-supervisor /opt/opensandbox-egress/supervisor
RUN chmod 0755 /opt/opensandbox-egress/cleanup.sh /opt/opensandbox-egress/egress \
        /opt/opensandbox-egress/supervisor \
    && chmod -R a+rX /var/egress/mitmscripts \
    && ln -s /opt/opensandbox-egress/egress /egress

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
# Upstream's: the supervisor restarts egress if it crashes, running the cleanup before each start
ENTRYPOINT ["/opt/opensandbox-egress/supervisor", \
            "--pre-start=/opt/opensandbox-egress/cleanup.sh", \
            "--name=egress", \
            "--grace-period=20s", \
            "--", \
            "/opt/opensandbox-egress/egress"]
