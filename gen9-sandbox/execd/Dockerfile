# syntax=docker/dockerfile:1
# OpenSandbox's execd (Apache-2.0), the daemon the server copies into every sandbox to run its
# commands (config.toml's execd_image). OpenSandbox builds it with Go 1.25.9 and grpc 1.82.1, in
# which govulncheck finds 21 vulnerabilities `/execd` reaches (docs/plans/manual-e2e.md, P6-D1c5c).
# So `/execd` is rebuilt from the same release's commit with a supported Go (1.25 left support when
# 1.27 came out), and with the modules OpenSandbox's main has since moved to, x/crypto one further
# (0.56.0, its SSH servers' fix). Go checks each module against its checksum database. The flags
# are upstream's (components/execd/Dockerfile).
# To upgrade: the image digest below (its signature checked: docs/development.md, "Signatures and
# provenance"), COMMIT (the release tag's commit) and the module versions.
FROM golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d AS builder
ARG COMMIT=48b0215f1bd097b31d0f022a44640e00c11ac49d
ADD https://github.com/opensandbox-group/OpenSandbox.git#${COMMIT} /src
ENV CGO_ENABLED=0
WORKDIR /src/components/execd
RUN go get google.golang.org/grpc@v1.83.2 golang.org/x/net@v0.58.0 golang.org/x/text@v0.41.0 \
       golang.org/x/crypto@v0.56.0 \
    && go build -trimpath -buildvcs=false -ldflags "-buildid= -B none \
       -X github.com/alibaba/opensandbox/internal/version.Version=v1.1.0 \
       -X github.com/alibaba/opensandbox/internal/version.GitCommit=${COMMIT}" -o /out/execd ./main.go

FROM opensandbox/execd:v1.1.0@sha256:6cf7dba2f21f0b536e100563d841ac58a9f31c2b0a081b7ac76796a24d6f47e2
COPY --from=builder --chmod=0755 /out/execd /execd
# What Gen9's server never takes from here: it copies /execd, bootstrap.sh, bwrap, the session gate
# and the launcher into each sandbox (release-1.1.0's services/docker/runtime.py). execd-ebpf (the
# eBPF audit build), execd.exe (Windows sandboxes) and the supervisor stay unused, with their Go
RUN rm /execd-ebpf /execd.exe /opensandbox-supervisor && apk upgrade --no-cache
