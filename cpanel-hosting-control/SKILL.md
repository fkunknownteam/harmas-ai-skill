---
name: cpanel-hosting-control
description: "Control cPanel shared hosting: upload, edit, delete files."
version: 0.1.0
author: Md Firoz (fkunknownteam), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cpanel, shared-hosting, deploy, files, php, upload, web-hosting]
    category: web-development
    related_skills: [publish-site]
---

# cPanel Hosting Control

Full control of a cPanel shared-hosting account from the terminal: list, read,
upload, edit, move, and delete files — plus MySQL database listing and any raw
UAPI call. No FTP (the data channel is blocked on many shared hosts), no scp,
no browser. Everything goes through the cPanel session-token UAPI and the
legacy API2 `fileop` for move/unlink.

## When to Use

Load this skill when the user asks to:

- **Deploy or update files** on shared hosting ("put this PHP on my server",
  "update the site", "the old version is still serving")
- **Read or edit a live file** without an editor ("show me what's in config.php",
  "change the API key in that file")
- **Delete or move** server files, create directories
- **List what's on the host** or find where a URL actually maps on disk
- **Any cPanel task** when FTP/scp is unavailable or silently failing

Don't use for: static-site publishing to GitHub/Cloudflare/Netlify Pages (use
`publish-site`), or anything needing a browser (use the browser tools).

## Prerequisites

- Python 3.8+ (stdlib only — `urllib`, `ssl`, `json`, `http.cookiejar`).
- A credential file at `~/.hermes/secrets/cpanel_<name>` containing ONE line:
  `user|password|host` (chmod 600). `<name>` is any label you pick for the host.
  Multiple hosts = multiple files. NEVER paste the password into chat or a
  command; the script reads it from the file.

## How to Run

All commands run via the `terminal` tool. The script is at
`scripts/cpanel.py` inside this skill directory.

```bash
S=<skill_dir>/scripts/cpanel.py
python3 $S myhost login                          # verify auth -> "OK token=..."
python3 $S myhost list public_html               # name type size
python3 $S myhost cat public_html/config.php -   # dump to stdout
python3 $S myhost put /tmp/new_api.php public_html/api   # safe overwrite deploy
python3 $S myhost move /home/USER/public_html/a.php /home/USER/public_html/b.php
python3 $S myhost rm /home/USER/public_html/junk.php     # no trash - careful
python3 $S myhost uapi Fileman list_files dir=public_html
python3 $S myhost db list                        # MySQL databases
```

## Procedure

1. **Login check.** Run `login` first. `OK token=...` means the credential file
   is correct. Completion criterion: exit code 0, token printed.
2. **Know the docroot before deploying.** Subdomain-to-directory mapping is NOT
   guessable and is frequently inverted from what you'd expect. Before deploying
   anywhere new, upload a uniquely-named probe file and fetch every candidate URL
   to find which directory actually serves which host. Completion criterion: one
   URL returns your probe marker and you know the exact directory it came from.
3. **Deploy with `put`.** It uploads as `<name>.hermesnew`, moves the current
   file to `.bak_<name>`, then moves the new one into place — rolling back if the
   final move fails. Completion criterion: `deployed: True`.
4. **Verify over HTTP — this is mandatory, not optional.** `put` can print
   `deployed: True` while the server still serves the OLD bytes (LiteSpeed
   caching). Fetch the live URL and compare a content marker or hash; a stale
   file also returns HTTP 200, so a status code alone proves nothing. Completion
   criterion: the response body contains your new marker.
5. **Edit live** with `cat` -> local patch -> `put`. Never edit blind.

## Pitfalls

- **Login form field is `pass`, not `password`** — `password` returns 401.
- **UAPI calls take NO `.json` suffix** — cPanel 114 returns "Illegal function
  name" for `/execute/Module/func.json`. Use `/execute/Module/func` with `?k=v`.
- **Basic auth alone cannot write.** Most write functions need the session
  token; `/execute/` with basic auth returns "Illegal function name".
- **`upload_files` cannot overwrite an existing file** — that's why `put` stages
  as `.hermesnew` and then `fileop`-moves it into place.
- **`fileop` takes ABSOLUTE paths** (`/home/<user>/public_html/...`) and the
  parameter is `sourcefiles` (plural). `list_files`/`upload_files` take
  HOME-RELATIVE dirs (`public_html/api`).
- **Stale-serve false positive.** A reported successful deploy can still serve
  old content. Always re-read over HTTP (step 4).
- **HTTPS is on port 2083** (not 2082). The script disables cert verification for
  the cPanel host only, which is needed for self-signed chains.
- **Subdomain docroots are often outside `public_html`** (e.g.
  `/home/<user>/<sub>.<domain>/`), and the mapping can be inverted from what the
  directory names suggest. Probe; don't assume.
- **Subdomain creation via API is blocked on some hosts** (Udomain/Subdomain/
  ZoneEdit modules absent). New subdomains may need the panel UI or the provider.
  Directory creation (`mkdir`) is likewise absent from Fileman on some hosts —
  upload a file into an existing dir instead, or use the panel file manager.
- **Large files (30MB+ APKs)** upload via `upload_files` but exceed interactive
  timeouts — run in a background terminal session and wait for the result.
- **`rm` is permanent.** No trash bin. `put` keeps a `.bak_` backup; direct `rm`
  does not.

## Verification

The deploy is done only when the LIVE URL serves the new content:

```bash
curl -s https://<domain>/<path> | grep -c "<your new content marker>"
```

Returns 1 (or more) for a real deploy, 0 for a stale-serve. For non-HTTP files,
use `cat` to read the file back and compare against what you uploaded.
