FROM docker/sandbox-templates:shell
USER root
RUN curl https://cursor.com/install -fsS | bash
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential jq openssh-client fd-find \
    && rm -rf /var/lib/apt/lists/*
USER agent
RUN mkdir -p ~/.cursor && \
    echo '{"permissions":{"allow":["*"],"deny":[]}}' > ~/.cursor/cli-config.json
