# Logging probes

## Sending the logs elsewhere (`ship/`, 2026-10-01)

What docs/logging.md, "Sending the logs elsewhere", rests on (docs/plans/manual-e2e.md, P6-C6;
ASVS 5.0 16.4.3). Grafana Alloy v1.20.1 read every container's log through Docker's API and sent
it over TLS to Loki 3.7.8, a throwaway stand-in for the operator's system, with its own
certificate authority (`certs.sh`). Both ran as their own Compose project next to Gen9's running
stacks, on Docker Desktop for Linux; nothing in Gen9 changed. Run it with `./certs.sh && docker
compose up -d`, read Loki with `./query.sh`, and remove it with `docker compose down -v`.

Why Alloy: Gen9's containers log through Docker's `local` driver, which writes to "an internal
storage" (Docker's docs), so a collector reads them through Docker's API, as `docker logs` does.
`loki.source.docker` (generally available) does, and keeps how far it has read each container in
a positions file. Vector's `docker_logs` source reads the same API but keeps no position (its
reference: delivery "best_effort", checkpoints off). The OpenTelemetry Collector (v0.162.0) has
no receiver for Docker's logs (`docker_stats` is metrics), and its `filelog` receiver reads files.

What it showed:

- **Every container's log arrives.** Within 20 s of starting, 25 of the 28 running containers had
  their lines in Loki, each labelled with its container and its Compose project (`stack`). The
  other three were the idle `ready` services, which write nothing. The first start sends what each
  container has logged so far.
- **The same lines on both sides.** The API's `audit {…}` lines of the last hour: 11 in Loki, 11
  in `docker logs`. Keycloak's event lines: 39 and 39. A one-shot job: Keycloak's `configure`,
  708 local lines, 698 in Loki, the other 10 empty (Loki keeps no empty line).
- **Nothing lost or repeated across a restart or an outage.** A container wrote a numbered line
  every 2 s. Alloy was stopped for 30 s, then Loki for 60 s, then Alloy was recreated with a new
  configuration. Loki held lines 1 to 249, each once and in order, the same as `docker logs`.
  After Loki's outage, the lines written while it was down arrived within about 2 minutes:
  Alloy retries with a growing wait.
- **Exited containers need a status filter.** Docker lists only running containers unless asked
  for a status. Without the filter, a container that printed a line and exited after 1 s was
  never read, though it stayed there, exited, for 40 s. A 3 s and an 8 s one were read. With
  `filter { name = "status", values = ["running", "exited"] }` the 1 s one was read too, and so
  were Gen9's one-shot jobs (gen9-agent's `migrate`, Keycloak's `configure`).
- **TLS is checked.** An Alloy trusting another certificate authority sent nothing: 6 tries in
  40 s, each a failed connection (`loki_write_request_duration_seconds_count{status_code="-1"}`,
  `loki_write_sent_entries_total` 0), and Loki logged `remote error: tls: bad certificate` for
  each. Alloy's own log at its default level said nothing about it in those 40 s, so watch those
  metrics (or Alloy's UI) to know that sending works.
- **What it can reach.** It reads Docker's socket, which is root on the host. Run it as the
  operator's, outside Gen9's stacks, and don't publish its UI (port 12345).
