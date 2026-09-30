# OpenSandbox's egress sidecar (Apache-2.0), pinned by digest, with a baseline no sandbox's policy
# can lift: deny.always. To upgrade: this digest and config.toml's [egress] image together.
FROM opensandbox/egress:v1.1.7@sha256:db7345d567b0970f384b8e3fa7a93a71b7f43d4b16bb2009de34096e9a87b3b5
COPY deny.always /var/egress/rules/deny.always
# A secret goes only to a TLS peer its binding trusts, not to wherever the Host header points:
# OpenSandbox #1758, its fix PR #1759 carried until a release has it (sni-binding.py). The check
# fails the build if system.py isn't v1.1.7's
COPY sni-binding.py /tmp/sni-binding.py
RUN echo "8c00c314cf45e97ae1b6c2151b114fedeb4c407c8c184d2b19904c8a540cb098  /var/egress/mitmscripts/system.py" | sha256sum -c - \
    && python3 /tmp/sni-binding.py /var/egress/mitmscripts/system.py \
    && rm /tmp/sni-binding.py
# Each change to the sandbox's rules is written here too, and a restarted egress process reads it
# before the rules the sandbox was created with: without it, egress restarted after a crash reopened
# the hosts gen9-agent had closed since (manual-e2e.md, P4-E4). OpenSandbox's own setting (#525)
ENV OPENSANDBOX_EGRESS_POLICY_FILE=/var/egress/policy.json
