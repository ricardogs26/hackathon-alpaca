FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Alpaca CLI (official, alpacahq/cli) — execution goes through it (MCP-or-CLI req).
ARG ALPACA_CLI_VERSION=0.0.14
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /tmp/cli.tgz \
       "https://github.com/alpacahq/cli/releases/download/v${ALPACA_CLI_VERSION}/cli_${ALPACA_CLI_VERSION}_linux_amd64.tar.gz" \
    && tar -xzf /tmp/cli.tgz -C /usr/local/bin alpaca \
    && rm /tmp/cli.tgz \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    && test -x /usr/local/bin/alpaca

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY optionwright/ ./optionwright/

EXPOSE 8080
CMD ["uvicorn", "optionwright.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
