# VortNotes Install Guide

VortNotes is distributed as a multi-architecture Docker image:

```text
vorticon/vortnotes:latest
```

Supported platforms:

- Unraid and other `linux/amd64` servers
- Windows/macOS/Linux PCs running Docker Desktop or Docker Engine
- Raspberry Pi 64-bit OS using `linux/arm64`

Always mount `/data` to a persistent host folder. VortNotes stores databases,
uploads, backups, configuration, logs, and the session secret there.

## Unraid

Use the supplied `unraid-template.xml` if you are adding VortNotes to an Unraid
template repository. For a manual Unraid Docker install, use:

```text
Repository:      vorticon/vortnotes:latest
WebUI:           http://[IP]:[PORT:9999]/
Container port:  8000
Host port:       9999
```

Required path:

```text
Name:           App Data
Host Path:      /mnt/cache/appdata/vortnotes
Container Path: /data
Access:         Read/Write
```

Required variable:

```text
NOTES_DATA_DIR=/data
```

Optional variables:

```text
WEB_CONCURRENCY=2
THREADS=2
TIMEOUT=120
```

Open VortNotes at:

```text
http://UNRAID-IP:9999
```

### Unraid HTTPS

The easiest HTTPS setup on Unraid is a reverse proxy. If using a trusted reverse
proxy, set:

```text
VORTNOTES_TRUST_PROXY_HEADERS=1
VORTNOTES_FORCE_SECURE_COOKIES=1
```

Direct HTTPS is also supported. Mount certificate files read-only, usually to
`/certs`, then either set these environment variables:

```text
VORTNOTES_TLS_CERT_FILE=/certs/fullchain.pem
VORTNOTES_TLS_KEY_FILE=/certs/privkey.pem
```

or open **Settings → Config** in VortNotes, enable direct HTTPS, and enter the
same in-container paths. Restart the container after changing direct HTTPS.

You can also click **Generate self-signed certificate** in **Settings → Config**
for a unique per-install certificate stored under `/data/config/tls/`. Restart
the container, then reconnect with `https://`. Browsers will warn because the
certificate is self-signed.

## PC with Docker Desktop

### Windows PowerShell

```powershell
mkdir C:\VortNotes\data

docker run -d `
  --name vortnotes `
  --restart unless-stopped `
  -p 8000:8000 `
  -e NOTES_DATA_DIR=/data `
  -v C:\VortNotes\data:/data `
  vorticon/vortnotes:latest
```

Open:

```text
http://localhost:8000
```

### Linux/macOS

```bash
mkdir -p ~/vortnotes-data

docker run -d \
  --name vortnotes \
  --restart unless-stopped \
  -p 8000:8000 \
  -e NOTES_DATA_DIR=/data \
  -v ~/vortnotes-data:/data \
  vorticon/vortnotes:latest
```

Open:

```text
http://localhost:8000
```

## Raspberry Pi

Use a 64-bit Raspberry Pi OS. Install Docker, then run:

```bash
mkdir -p /home/pi/vortnotes-data

docker run -d \
  --name vortnotes \
  --restart unless-stopped \
  -p 8000:8000 \
  -e NOTES_DATA_DIR=/data \
  -v /home/pi/vortnotes-data:/data \
  vorticon/vortnotes:latest
```

Open:

```text
http://RASPBERRY-PI-IP:8000
```

## Docker Compose

Create `docker-compose.yml`:

```yaml
services:
  vortnotes:
    image: vorticon/vortnotes:latest
    container_name: vortnotes
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      NOTES_DATA_DIR: /data
    volumes:
      - ./vortnotes-data:/data
```

Start:

```bash
docker compose up -d
```

Update:

```bash
docker compose pull
docker compose up -d
```

## Backups

Back up the entire `/data` host folder. In-app database ZIP backups are stored
under:

```text
/data/backups
```

For Unraid, back up:

```text
/mnt/cache/appdata/vortnotes
```

or whichever host path you mapped to `/data`.
