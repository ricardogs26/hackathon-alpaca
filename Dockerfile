FROM python:3.12-slim

# The Alpaca CLI is installed here so execution goes through it (MCP-or-CLI req).
# Pinned install lands with the broker layer; kept as a build arg for now.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY optionwright/ ./optionwright/

EXPOSE 8080
CMD ["uvicorn", "optionwright.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
