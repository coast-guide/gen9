# Gen9's own images, built once for each platform Gen9 runs on, then run by digest by every
# deployment (docs/plans/deploy.md, U1). The contexts are the ones each stack's compose.yaml builds
# for development (`make up`); scripts/check-images.py, in `make config`, fails if they differ.
#
#   docker buildx bake --print                                    what would be built
#   docker buildx bake --set '*.platform=linux/amd64' --load      build here, for this machine
#
# CI builds each target with Docker's reusable bake workflow (.github/workflows/images.yml), which
# replaces the tags below with ghcr.io/coast-guide/<target> and pushes by digest.

variable "REGISTRY" {
  default = "ghcr.io/coast-guide"
}

variable "TAG" {
  default = "dev"
}

group "default" {
  targets = [
    "gen9-agent",
    "gen9-ui",
    "gen9-keycloak",
    "gen9-postgres",
    "gen9-sandbox",
    "gen9-sandbox-egress",
    "gen9-sandbox-execd",
  ]
}

target "_common" {
  platforms = ["linux/amd64", "linux/arm64"]
  labels = {
    "org.opencontainers.image.source"   = "https://github.com/coast-guide/gen9"
    "org.opencontainers.image.licenses" = "Apache-2.0"
  }
}

target "gen9-agent" {
  inherits = ["_common"]
  context  = "gen9-agent"
  tags     = ["${REGISTRY}/gen9-agent:${TAG}"]
}

target "gen9-ui" {
  inherits = ["_common"]
  context  = "gen9-ui"
  tags     = ["${REGISTRY}/gen9-ui:${TAG}"]
}

target "gen9-keycloak" {
  inherits = ["_common"]
  context  = "gen9-keycloak"
  tags     = ["${REGISTRY}/gen9-keycloak:${TAG}"]
}

target "gen9-postgres" {
  inherits = ["_common"]
  context  = "gen9-postgres"
  tags     = ["${REGISTRY}/gen9-postgres:${TAG}"]
}

target "gen9-sandbox" {
  inherits = ["_common"]
  context  = "gen9-sandbox"
  tags     = ["${REGISTRY}/gen9-sandbox:${TAG}"]
}

target "gen9-sandbox-egress" {
  inherits = ["_common"]
  context  = "gen9-sandbox/egress"
  tags     = ["${REGISTRY}/gen9-sandbox-egress:${TAG}"]
}

target "gen9-sandbox-execd" {
  inherits = ["_common"]
  context  = "gen9-sandbox/execd"
  tags     = ["${REGISTRY}/gen9-sandbox-execd:${TAG}"]
}
