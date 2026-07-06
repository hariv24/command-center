# Deploying the Command Center to a fresh GCP instance

## 1. Create the instance (console.cloud.google.com)
- Compute Engine → VM Instances → Create Instance
- Machine type: `e2-medium` (2 vCPU/4GB) is enough for this workload; bigger works too, just costs more
- Boot disk: Ubuntu 22.04 or 26.04 LTS, 30GB
- Firewall: check "Allow HTTP traffic" and "Allow HTTPS traffic"
- Reserve a static IP: VPC Network → IP Addresses → find the instance's ephemeral IP → Promote to static

## 2. SSH access
- Compute Engine → Metadata → SSH Keys → Add Item → paste a public key (generate one locally: `ssh-keygen -t ed25519 -f key/<name> -N ""`)
- This is project-wide, works for any instance

## 3. Point `.deploy.env` at it
```
DEPLOY_HOST=<static IP>
DEPLOY_KEY=key/<your-key>
DEPLOY_USER=ubuntu
CC_DOMAIN=cc.thinkgalactic.in
```

## 4. Ship the code
```bash
./deploy.sh   # first run: packages + uploads + extracts, but venv doesn't exist yet
```

## 5. Copy secrets + RAG database (not in git)
```bash
scp -i key/<your-key> .env ubuntu@<ip>:~/agent/.env
rsync -avz -e "ssh -i key/<your-key>" chroma_db/ ubuntu@<ip>:~/agent/chroma_db/
```

## 6. Bootstrap the server
```bash
ssh -i key/<your-key> ubuntu@<ip> "chmod +x ~/agent/deploy/setup_server.sh; ~/agent/deploy/setup_server.sh"
```
This is idempotent — creates a 2GB swapfile (the thing that would have saved the Oracle box), installs pinned deps into a venv, installs Caddy for auto-TLS, writes a systemd unit with `Restart=always` + a memory cap, and installs the cron jobs for the daily briefs.

## 7. DNS cutover
Point `cc.thinkgalactic.in`'s A-record at the new static IP. Caddy issues a TLS cert automatically on first HTTPS request to that domain — no manual certbot step.

## 8. Verify
```bash
curl https://cc.thinkgalactic.in/healthz
```
Should return `{"status":"ok", "chroma_db": true, "groq_key": true, "openrouter_key": true, ...}`.

## Redeploying after code changes
Just `./deploy.sh` — it re-syncs code, reinstalls any new pinned deps, restarts the service. Secrets and the RAG database aren't touched (they're excluded from the tarball on purpose).
