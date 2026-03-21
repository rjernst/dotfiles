FROM docker/sandbox-templates:claude-code
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential jq openssh-client fd-find \
    && rm -rf /var/lib/apt/lists/*
USER agent
