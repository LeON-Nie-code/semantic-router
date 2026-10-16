# Build the operator binary
# Using RHEL 10 UBI Golang builder image
# Build context must be the repository root so local replace directives can reach src/semantic-router.
ARG GO_IMAGE=registry.access.redhat.com/ubi10/go-toolset:latest
ARG RUNTIME_IMAGE=registry.access.redhat.com/ubi10/ubi-minimal:latest
FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS builder

# BuildKit supplies the target architecture for native and cross builds.
ARG TARGETARCH

USER root

WORKDIR /workspace

# Copy the module manifests needed by local replace directives first for better caching
RUN mkdir -p deploy/operator src/semantic-router
COPY deploy/operator/go.mod deploy/operator/go.sum deploy/operator/
COPY src/semantic-router/go.mod src/semantic-router/go.sum src/semantic-router/

WORKDIR /workspace/deploy/operator

# Cache deps before building and copying source so that we don't need to re-download as much
# and so that source changes don't invalidate our downloaded layer
# Pipe falls back to direct on any proxy error; comma only on 404 and 410
ARG GOPROXY="https://proxy.golang.org|direct"
ENV GOPROXY=${GOPROXY}
RUN go mod download

# Copy the go source
COPY deploy/operator/ /workspace/deploy/operator/
COPY src/semantic-router/ /workspace/src/semantic-router/

# Build
RUN CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} go build -a -o /workspace/manager main.go

# Use RHEL 10 UBI minimal for the final image
FROM ${RUNTIME_IMAGE}

# Install necessary runtime dependencies
RUN if command -v microdnf >/dev/null 2>&1; then \
      microdnf install -y shadow-utils ca-certificates && microdnf clean all; \
    elif command -v apt-get >/dev/null 2>&1; then \
      apt-get update && apt-get install -y --no-install-recommends ca-certificates passwd && \
        rm -rf /var/lib/apt/lists/*; \
    else \
      echo "Operator runtime requires a UBI- or Debian-compatible image" >&2; exit 1; \
    fi

WORKDIR /

# Create non-root user
RUN useradd -u 65532 -r -g 0 -m -s /sbin/nologin \
    -c "semantic-router-operator user" semantic-router-operator

# Copy the operator binary from builder
COPY --from=builder /workspace/manager .

# Set ownership and permissions
RUN chown semantic-router-operator:0 /manager && \
    chmod 0755 /manager

USER 65532:0

ENTRYPOINT ["/manager"]
