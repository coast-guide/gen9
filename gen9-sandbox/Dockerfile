# OpenSandbox's server (Apache-2.0), pinned by digest, started through Gen9's launcher (launch.py):
# every sandbox's ports on SANDBOX_PUBLISH_HOST, never on all interfaces. To upgrade: the digest,
# execd and egress in config.toml, then check launch.py still applies (gen9-agent/explore/sandbox).
FROM opensandbox/server:release-1.1.0@sha256:68ca0212a2749b2c73096ce2ec0264455c64442c45f81007db442f52bf84c9d1
COPY launch.py /opt/gen9/launch.py
ENTRYPOINT ["/app/.venv/bin/python", "/opt/gen9/launch.py"]
CMD ["--config", "/etc/opensandbox/config.toml"]
