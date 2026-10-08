# syntax=docker/dockerfile:1
# gen9-postgres: Postgres's official image (18) with pgvector from the PostgreSQL project's own apt
# repository (apt.postgresql.org, signed; the image carries its key) and pg_textsearch, BM25
# full-text search (timescale/pg_textsearch, PostgreSQL licence). Not pgvector's own image: it isn't
# rebuilt when Debian ships fixes (its perl, openssl and glibc were behind, while the
# official image's rebuild of the same day had them). pg_textsearch's release packages are fetched
# by checksum, and the one for the build's architecture is installed. To upgrade pg_textsearch: bump
# the version and both checksums (shasum -a 256 on the release's pg18 zips).
FROM busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e AS packages
ARG TARGETARCH
ADD --checksum=sha256:c2ce0585a7f09ec7fc441f1fba11888d6ad3ab51b1851d4f24a7457905d0519f \
    https://github.com/timescale/pg_textsearch/releases/download/v1.4.0/pg-textsearch-v1.4.0-pg18-amd64.zip /zips/amd64.zip
ADD --checksum=sha256:f65bc4f46393ca66c78e4da71ae8c54d0aef9975eaf3d5b6836d0f4570c68c64 \
    https://github.com/timescale/pg_textsearch/releases/download/v1.4.0/pg-textsearch-v1.4.0-pg18-arm64.zip /zips/arm64.zip
RUN mkdir /out && unzip /zips/${TARGETARCH}.zip -d /out

FROM postgres:18.6-trixie@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722
ARG PGVECTOR_VERSION=0.8.6-1.pgdg13+2
COPY --from=packages /out/ /tmp/pg_textsearch/
RUN apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-18-pgvector=${PGVECTOR_VERSION}" \
    && dpkg -i /tmp/pg_textsearch/*.deb \
    && rm -rf /tmp/pg_textsearch /var/lib/apt/lists/*
