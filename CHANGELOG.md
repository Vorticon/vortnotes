# Changelog

This project follows Semantic Versioning.

## 1.0.8 - 2026-06-22

- Added an admin Settings button to generate a unique per-install self-signed
  HTTPS certificate and private key under `/data/config/tls/`.
- Generated self-signed certificates automatically enable direct HTTPS after
  restart without requiring a bundled/shared private key.

## 1.0.7 - 2026-06-22

- Rebuilt the Docker runtime on Alpine Linux to reduce inherited base-image
  vulnerability noise.
- Updated Flask and Bleach to patched versions.
- Removed pip, wheel, and setuptools from the final runtime image after
  dependency installation.

## 1.0.6 - 2026-06-21

- Database-selector password and logout controls now update immediately when
  the dropdown selection changes.
- Per-database logout explicitly targets the dropdown-selected database.

## 1.0.5 - 2026-06-21

- Database-selector password prompts now reflect the selected database's active
  login state.
- Database logout now applies only to the selected database; other remembered
  database sessions and the separate admin session remain active.

## 1.0.4 - 2026-06-21

- A remembered database login now permits switching among protected databases
  without repeated passwords until explicit logout.
- Clarified the database selector wording and remembered-session state.

## 1.0.3 - 2026-06-21

- Moved direct HTTPS controls into the admin Config accordion.
- Restored the Read without password checkbox for every database row.
- Allowed built-in apps and Home Assistant scene/script tiles when a protected
  database grants read-without-password access, while retaining write locks.

## 1.0.2 - 2026-06-21

- Moved direct HTTPS into its own expanded admin card on `/settings` so the
  switch and certificate paths are immediately visible.
- Separated HTTPS saving from upload-limit configuration.

## 1.0.1 - 2026-06-21

- Added direct-HTTPS controls under the admin Config panel.
- Added persisted certificate and private-key paths with startup validation.
- Updated the Unraid template to run as `nobody:users` (`99:100`) and added an
  optional read-only certificate-folder mapping.

## 1.0.0 - 2026-06-21

- First distribution-ready release.
- Added notes, media/content organization, backups, themes, and built-in apps.
- Added versioned schema tracking and automatic upgrades for existing data.
- Added direct HTTPS and trusted reverse-proxy deployment options.
- Added non-root containers, health checks, security headers, CI, release
  images, vulnerability scanning, SBOM generation, and image signing.
