# syntax=docker/dockerfile:1.7
# gen9-sbom: Syft and Grype, the tools of `make sbom` and `make scan` (scripts/sbom.sh;
# docs/plans/manual-e2e.md, P6-D1). Taken from their release tarballs, each fetched by the checksum
# its release lists, in a checksums file that cosign verified as signed by Anchore's release
# workflow. Their own container images carry no signature. On distroless's static image, signed by
# its project, which holds certificate authorities for Grype's database download and nothing more.
#
# To update a tool: download its release's checksums.txt with .sig and .pem, verify them with
#   cosign verify-blob <tool>_<v>_checksums.txt --certificate <…>.pem --signature <…>.sig \
#     --certificate-identity https://github.com/anchore/<tool>/.github/workflows/release.yaml@refs/heads/main \
#     --certificate-oidc-issuer https://token.actions.githubusercontent.com
# then put the version and its linux_amd64 and linux_arm64 checksums below.
FROM busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e AS tools
ARG TARGETARCH
ADD --checksum=sha256:caeedb81fb0491615f1ebd1761e4145d41ee86dd2cc7bf80669f9f5ad9d6133d \
    https://github.com/anchore/syft/releases/download/v1.52.0/syft_1.52.0_linux_amd64.tar.gz /dl/syft-amd64.tar.gz
ADD --checksum=sha256:c46d5e4c28e12aa4c5becfaa343ef1c7f89045b6b895f2c21d471c62db09c706 \
    https://github.com/anchore/syft/releases/download/v1.52.0/syft_1.52.0_linux_arm64.tar.gz /dl/syft-arm64.tar.gz
ADD --checksum=sha256:3fa2dc4b924621ab65404cf08d0b8438d896d80ab949c9d5a4ca283c36004c9b \
    https://github.com/anchore/grype/releases/download/v0.119.0/grype_0.119.0_linux_amd64.tar.gz /dl/grype-amd64.tar.gz
ADD --checksum=sha256:29f0ec7c549ddb0e2b6a0ca714851f7399438afc399b80c12808e065edc9a8f8 \
    https://github.com/anchore/grype/releases/download/v0.119.0/grype_0.119.0_linux_arm64.tar.gz /dl/grype-arm64.tar.gz
RUN mkdir -p /out/bin /out/cache \
    && tar -xzf /dl/syft-${TARGETARCH}.tar.gz -C /out/bin syft \
    && tar -xzf /dl/grype-${TARGETARCH}.tar.gz -C /out/bin grype

FROM gcr.io/distroless/static-debian13:nonroot@sha256:e2e927ec666bae08560abb3c55d0659eceabb657f56b6782ab500a9fc7f555e3
COPY --from=tools /out/bin/ /usr/local/bin/
# Grype's database, in a volume (scripts/sbom.sh): a new volume takes this folder's owner
COPY --from=tools --chown=65532:65532 /out/cache /cache
ENV GRYPE_DB_CACHE_DIR=/cache GRYPE_CHECK_FOR_APP_UPDATE=false SYFT_CHECK_FOR_APP_UPDATE=false
