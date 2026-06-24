# EC2 Deployment

Recommended shape: a separate small EC2 instance called
`economist-rss-server`, independent from Phoneclaw.

## Instance

Start small:

- Ubuntu 24.04 LTS
- `t3.micro`, `t3.small`, or equivalent
- 20 GB gp3 volume
- outbound HTTPS access
- inbound access only from your trusted network, VPN, Tailscale, or reverse
  proxy

The generated RSS feed contains subscriber-only article text, so avoid exposing
it publicly.

## Install

```bash
sudo apt-get update
sudo apt-get install -y git python3.12-venv
sudo mkdir -p /opt/economist-newspaper-rss-feed /etc/economist-rss /var/lib/economist-rss
sudo chown -R "$USER":"$USER" /opt/economist-newspaper-rss-feed /var/lib/economist-rss
git clone https://github.com/andrewfurman/economist-newspaper-rss-feed.git /opt/economist-newspaper-rss-feed
cd /opt/economist-newspaper-rss-feed
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[browser]'
.venv/bin/python -m playwright install chromium
cp feeds.example.toml /etc/economist-rss/feeds.toml
cp sample.env /etc/economist-rss/real.env
chmod 600 /etc/economist-rss/real.env
```

Edit `/etc/economist-rss/real.env` with your subscriber credentials and a
private refresh token.

In `/etc/economist-rss/feeds.toml`, use persistent data paths:

```toml
output_path = "/var/lib/economist-rss/economist-fulltext.xml"
database_path = "/var/lib/economist-rss/economist-rss.sqlite3"
refresh_interval_seconds = 600
article_lookback_days = 30
max_articles_per_refresh = 2
exclude_url_patterns = []
world_in_brief_enabled = true
world_in_brief_refresh_interval_seconds = 3600
# Leave this empty when copying a Playwright storage_state JSON from another host.
browser_user_data_dir = ""
browser_storage_state = "/var/lib/economist-rss/browser-state.json"
```

Authenticate once:

```bash
/opt/economist-newspaper-rss-feed/.venv/bin/economist-rss auth \
  --env-file /etc/economist-rss/real.env \
  --config /etc/economist-rss/feeds.toml
```

## systemd

Copy the unit files:

```bash
sudo cp deploy/economist-rss.service /etc/systemd/system/economist-rss.service
sudo cp deploy/economist-rss-refresh.service /etc/systemd/system/economist-rss-refresh.service
sudo cp deploy/economist-rss-refresh.timer /etc/systemd/system/economist-rss-refresh.timer
sudo systemctl daemon-reload
sudo systemctl enable --now economist-rss.service
sudo systemctl enable --now economist-rss-refresh.timer
```

The feed will listen on `127.0.0.1:8080` by default. Put it behind a private
reverse proxy or VPN-only access path before adding it to an RSS reader.

## Operations

Manual refresh:

```bash
sudo systemctl start economist-rss-refresh.service
```

Logs:

```bash
journalctl -u economist-rss.service -f
journalctl -u economist-rss-refresh.service -n 100
journalctl -u economist-rss-refresh.service --since "24 hours ago" | grep 'article_fetch'
```

The refresh timer runs every 10 minutes and the scheduled service does not use
`--force`, so failed or rate-limited articles remain subject to backoff. The
World in Brief special fetch runs at most once per hour. Use manual forced
refreshes only for deliberate one-off debugging.

Back up `/var/lib/economist-rss`, not just the repository. That directory holds
the SQLite article cache and browser session state.
