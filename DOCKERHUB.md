# VortNotes

Self-hosted notes, content dashboards, uploads, Home Assistant shortcuts, focus
tools, and built-in mini apps for Docker, Unraid, PCs, servers, and Raspberry Pi.

```bash
docker run -d \
  --name vortnotes \
  --restart unless-stopped \
  -p 8000:8000 \
  -e NOTES_DATA_DIR=/data \
  -v ~/vortnotes-data:/data \
  vorticon/vortnotes:latest
```

Open `http://SERVER-IP:8000`.

## Image

```text
vorticon/vortnotes:latest
```

Multi-architecture support:

- `linux/amd64` for PCs, servers, and Unraid
- `linux/arm64` for Raspberry Pi 64-bit and other ARM64 systems

## Persistent data

Always mount `/data` to a host folder.

```text
/data/dbs/                SQLite database files
/data/uploads/            attachments, content files, icons, backgrounds
/data/backups/            DB ZIP backups
/data/config/config.json  app settings
/data/.secret_key         stable Flask session secret
/data/logs/               logs
```

## Unraid

Recommended manual container settings:

```text
Repository:      vorticon/vortnotes:latest
WebUI:           http://[IP]:[PORT:9999]/
Container port:  8000
Host port:       9999
```

Required path:

```text
Host Path:      /mnt/cache/appdata/vortnotes
Container Path: /data
Access:         Read/Write
```

Required variable:

```text
NOTES_DATA_DIR=/data
```

The GitHub repository includes a full Unraid template and detailed install
instructions.

## HTTPS

Use a reverse proxy for public deployments, or enable direct HTTPS in
**Settings → Config** after mounting certificate files read-only into the
container. Environment variables are also supported:

```text
VORTNOTES_TLS_CERT_FILE=/certs/fullchain.pem
VORTNOTES_TLS_KEY_FILE=/certs/privkey.pem
```

## More information

Source, install docs, changelog, and Unraid template:

https://github.com/Vorticon/vortnotes
