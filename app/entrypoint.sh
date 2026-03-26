#!/bin/bash
set -e

# Start Tailscale daemon
tailscaled --tun=userspace-networking --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock &
sleep 2

# Authenticate
tailscale up --authkey="${TS_AUTHKEY}" --hostname=lab-data-uploader
echo "Waiting for Tailscale connection..."
tailscale status --peers=false

echo "Tailscale connected. Checking peer connectivity..."
tailscale status

# Mount NFS shares from lab PCs
# NFS_MOUNTS format: "host1:/share1:/mnt/lab1,host2:/share2:/mnt/lab2"
if [ -n "$NFS_MOUNTS" ]; then
  IFS=',' read -ra MOUNTS <<< "$NFS_MOUNTS"
  for mount_spec in "${MOUNTS[@]}"; do
    IFS=':' read -r host share mountpoint <<< "$mount_spec"
    mkdir -p "$mountpoint"
    echo "Mounting $host:$share -> $mountpoint"
    mount -t nfs -o ro,nolock,soft,timeo=30 "$host:$share" "$mountpoint" || echo "WARNING: Failed to mount $host:$share"
  done
  echo "NFS mounts complete."
else
  echo "WARNING: NFS_MOUNTS not set, no shares mounted."
fi

echo "Starting uploader agent..."
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
