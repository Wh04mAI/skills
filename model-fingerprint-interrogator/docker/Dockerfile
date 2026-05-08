FROM node:24-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /workspace
RUN mkdir -p /workspace/.empty && cd /workspace/.empty && git init -q

ENV CLAUDE_CODE_DISABLE_TELEMETRY=1
ENV CI=1

WORKDIR /workspace/.empty

ENTRYPOINT ["claude"]
