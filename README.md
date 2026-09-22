# harmas-ai-skill

Hermes Agent skills — practical automation for real-world hosting and ops work.

These are [Hermes Agent](https://github.com/NousResearch/hermes-agent) optional
skills. Install with:

```bash
hermes skills install <owner>/<repo>/<skill-name>
```

or copy the skill directory into `~/.hermes/skills/`.

## Skills

### cpanel-hosting-control

Full control of a cPanel shared-hosting account from the terminal: list, read,
upload, edit, move, and delete files, plus raw UAPI access and MySQL listing.
No FTP, no scp, no browser — everything over the cPanel session-token UAPI.

- **Safe deploys:** `put` uploads to a staging name, backs up the live file to
  `.bak_<name>`, swaps in the new one, and rolls back if the swap fails.
- **Mandatory live verification:** documents the LiteSpeed stale-serve trap
  where a deploy reports success but still serves old bytes.
- **Encodes real host quirks:** the login form field is `pass` (not
  `password`), UAPI takes no `.json` suffix, `fileop` needs absolute paths,
  subdomain docroots are often inverted from what you'd expect.

Credentials live in `~/.hermes/secrets/cpanel_<name>` as `user|password|host`
(chmod 600) — never pasted into chat or commands.

```bash
python3 cpanel-hosting-control/scripts/cpanel.py myhost login
python3 cpanel-hosting-control/scripts/cpanel.py myhost list public_html
python3 cpanel-hosting-control/scripts/cpanel.py myhost put ./new_api.php public_html/api
python3 cpanel-hosting-control/scripts/cpanel.py myhost cat public_html/config.php -
```

Verified against a live cPanel/LiteSpeed shared host (cPanel 114).
