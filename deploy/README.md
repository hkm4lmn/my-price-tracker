# Deploying MY Price Tracker

One small Linux server runs everything with Docker Compose: PostgreSQL, the web app, the worker, and Caddy for HTTPS. These steps assume Ubuntu 24.04 on a DigitalOcean droplet (the smallest size is enough), but any Linux host with Docker works.

## 1. Things to get first

- **A server.** With the [GitHub Student Developer Pack](https://education.github.com/pack), DigitalOcean credit covers a small droplet for many months.
- **A domain.** The Student Pack includes a free one-year domain from Namecheap. A free [DuckDNS](https://www.duckdns.org) subdomain also works.
- **A Telegram bot.** Message [@BotFather](https://t.me/BotFather), send `/newbot`, and keep the token it gives you.

## 2. Set up the server

Point the domain's `A` record at the server's IP address, then on the server:

```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out and back in afterwards

# Firewall: SSH, HTTP and HTTPS only
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable

# The code
git clone https://github.com/hkm4lmn/my-price-tracker.git ~/my-price-tracker
cd ~/my-price-tracker
cp .env.example .env
```

Edit `.env` and fill in `TELEGRAM_BOT_TOKEN`, `BOT_USERNAME`, `DOMAIN`, a random `TELEGRAM_WEBHOOK_SECRET` and a random `POSTGRES_PASSWORD`. Generate the random values with `openssl rand -hex 24` (letters and digits only, so they are safe inside the database URL). Then lock the file down with `chmod 600 .env`.

## 3. Start it

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file .env up -d --build
docker compose -f deploy/docker-compose.prod.yml --env-file .env ps
```

`migrate` should show as exited (0), and `db`, `web`, `worker` and `caddy` as running. Caddy gets an HTTPS certificate on the first request to `https://<your domain>`, which can take a minute.

Then tell Telegram where to send messages:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file .env run --rm web python -m scripts.set_webhook
```

Message the bot `/start` to check it answers.

## 4. Nightly backups

```bash
sudo mkdir -p /var/backups/price-tracker && sudo chown $USER /var/backups/price-tracker
crontab -e
```

Add this line to back up the database at 03:00 every night and keep 7 days:

```
0 3 * * * ~/my-price-tracker/deploy/backup.sh >> ~/backup.log 2>&1
```

To restore: `gunzip -c /var/backups/price-tracker/<date>.sql.gz | docker compose -f deploy/docker-compose.prod.yml --env-file .env exec -T db psql -U tracker tracker`

## 5. Deploying updates from GitHub

The **deploy** workflow (Actions tab, then "Run workflow") connects over SSH, pulls `main` and rebuilds. It needs four repository secrets (Settings, then Secrets and variables, then Actions):

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | The server's IP address or domain |
| `DEPLOY_USER` | The Linux user that owns `~/my-price-tracker` |
| `DEPLOY_SSH_KEY` | A private key made just for deploys (`ssh-keygen -t ed25519 -f deploy_key -N ""`); add `deploy_key.pub` to the server's `~/.ssh/authorized_keys` |
| `DEPLOY_KNOWN_HOSTS` | The output of `ssh-keyscan <server>`, run from your own computer, so the workflow can verify it's talking to your server |

## Useful commands

```bash
# Logs
docker compose -f deploy/docker-compose.prod.yml --env-file .env logs -f worker
# Database shell
docker compose -f deploy/docker-compose.prod.yml --env-file .env exec db psql -U tracker tracker
```
