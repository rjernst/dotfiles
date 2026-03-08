FROM debian:bookworm-slim

ARG EXTRA_PACKAGES=""

# Essential tools + ripgrep/fd
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      git curl wget jq openssh-client ca-certificates \
      build-essential less ripgrep fd-find \
      nodejs npm \
      $EXTRA_PACKAGES && \
    rm -rf /var/lib/apt/lists/*

# Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Non-root user
RUN useradd -m -s /bin/bash ralph
USER ralph

WORKDIR /work

COPY --chown=ralph:ralph entrypoint.sh /home/ralph/entrypoint.sh
RUN chmod +x /home/ralph/entrypoint.sh

ENTRYPOINT ["/home/ralph/entrypoint.sh"]
