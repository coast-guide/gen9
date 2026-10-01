# OpenSandbox's egress sidecar (Apache-2.0), pinned by digest, with a baseline no sandbox's policy
# can lift: deny.always. To upgrade: this digest and config.toml's [egress] image together.
FROM opensandbox/egress:release-1.1.0@sha256:56429c89b7175c2a24af62ca93a99776f03ea67a0beb74d63c8ddb34ff16fd97
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
