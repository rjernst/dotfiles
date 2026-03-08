FROM debian:bookworm-slim

ARG EXTRA_PACKAGES=""
ARG HOST_UID=1000

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

# Non-root user with host UID so bind-mounted files are readable
RUN useradd -m -s /bin/bash -u $HOST_UID ralph
USER ralph

WORKDIR /work

COPY --chown=ralph:ralph entrypoint.sh /home/ralph/entrypoint.sh
RUN chmod +x /home/ralph/entrypoint.sh

ENTRYPOINT ["/home/ralph/entrypoint.sh"]
