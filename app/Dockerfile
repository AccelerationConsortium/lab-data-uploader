FROM tailscale/tailscale:latest AS tailscale

FROM python:3.11-slim

WORKDIR /app

# Tailscale binaries
COPY --from=tailscale /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale
RUN apt-get update && apt-get install -y --no-install-recommends nfs-common curl && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /var/lib/tailscale /var/run/tailscale /mnt/labdata /data/state /data/logs

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD pgrep -f "uploader-agent" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
