# Deploying to Hetzner Cloud

From a fresh server to the site on your domain with HTTPS. About 30–40 minutes the first time.

What runs on the server (all in Docker):

| Container | Role |
| --- | --- |
| `caddy` | Public entry point: HTTPS (Let's Encrypt, renewed automatically), static files and photos, proxy to the app |
| `web` | Django + Wagtail under gunicorn; applies migrations and collects static files on start |
| `worker` | Background jobs: expires unconfirmed requests, completes past bookings |
| `db` | PostgreSQL 16; data in the `pgdata` volume |

Only ports 22, 80 and 443 are open. The database is not reachable from outside.

---

## 1. Buy the server and the domain

1. Hetzner Cloud Console → **Add server**:
   - Location: Falkenstein, Nuremberg or Helsinki (all EU).
   - Image: **Ubuntu 24.04**.
   - Type: 2 vCPU, 4 GB RAM is plenty (can be resized later).
   - **SSH key**: add your public key (`~/.ssh/id_ed25519.pub`). Without it the setup script keeps password login on.
   - **Backups**: turn on (daily snapshots of the whole server, kept 7 days).
2. Note the server's IPv4 address.
3. At your domain registrar, create DNS records:
   - `A` record for `@` (the bare domain) → server IP
   - `A` record for `www` → server IP
   - (optional) `AAAA` records with the server's IPv6

   Check that DNS has updated before step 4: `dig +short yourdomain.com` should print the IP.

## 2. Prepare the server (once)

```bash
ssh root@SERVER_IP
# copy the script from your computer, or paste it into a file:
scp deploy/setup-server.sh root@SERVER_IP:/root/     # run this on your computer
bash /root/setup-server.sh                            # run this on the server
```

The script installs Docker, enables automatic security updates and the firewall,
creates the user `oxi` with your SSH key, disables password logins and adds swap.

From now on log in as `oxi`:

```bash
ssh oxi@SERVER_IP
```

## 3. Put the code on the server

With a Git repository (recommended):

```bash
git clone git@github.com:YOU/oxi_site.git ~/oxi_site
```

Or copy the folder from your computer:

```bash
rsync -av --exclude .venv --exclude media --exclude staticfiles ./oxi_site/ oxi@SERVER_IP:~/oxi_site/
```

## 4. Configure

```bash
cd ~/oxi_site/deploy
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → POSTGRES_PASSWORD
python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # → SECRET_KEY
nano .env
```

Fill in at least `DOMAIN`, `ACME_EMAIL`, `POSTGRES_PASSWORD`, `SECRET_KEY`.
Consider changing `ADMIN_URL` and `CMS_URL` to less guessable paths.

## 5. Start

```bash
cd ~/oxi_site/deploy
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps          # all four should be "running"/"healthy"
docker compose -f docker-compose.prod.yml logs -f caddy   # watch the certificate being issued, Ctrl+C to exit
```

Create the admin account and the starting content:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
docker compose -f docker-compose.prod.yml exec web python manage.py seed_demo
```

Open `https://yourdomain.com`. Then:

- `https://yourdomain.com/cms/` — pages, photos, services and prices, contacts
  (**Settings → Site settings**). In **Settings → Sites**, set the hostname to your domain.
- `https://yourdomain.com/manage-admin/` — bookings, clients, working hours, booking policy.

## 6. Daily backups

```bash
echo "30 3 * * * oxi /home/oxi/oxi_site/deploy/backup.sh >> /var/backups/oxi/backup.log 2>&1" \
  | sudo tee /etc/cron.d/oxi-backup
~/oxi_site/deploy/backup.sh            # run once now to check
ls -lh /var/backups/oxi
```

Backups stay on the same server; Hetzner snapshots cover the server dying, but for
real safety also copy `/var/backups/oxi` elsewhere (for example a Hetzner Storage Box
with `rsync` in the same cron job).

### Restore the database from a backup

```bash
cd ~/oxi_site/deploy
docker compose -f docker-compose.prod.yml stop web worker
docker compose -f docker-compose.prod.yml exec -T db \
  pg_restore -U oxi -d oxi --clean --if-exists < /var/backups/oxi/db_YYYY-MM-DD_HHMM.dump
docker compose -f docker-compose.prod.yml start web worker
```

Photos: `docker compose -f docker-compose.prod.yml exec -T web tar -C /app/media -xzf - < /var/backups/oxi/media_….tar.gz`

---

## 7. Automatic deploys from GitHub (CI/CD)

`.github/workflows/ci.yml` runs on every push and pull request:

| Stage | When | What |
| --- | --- | --- |
| lint | always | `ruff check`, `ruff format --check` |
| test | always | Django checks, missing migrations, `pytest` against PostgreSQL 16 |
| build | push to `develop` or `main` | Docker image → `ghcr.io/<login>/oxi_site:sha-<commit>` (plus a `:develop` / `:main` tag), smoke test |
| deploy | push to `main`, if enabled | SSH to the server → `deploy/deploy.sh sha-<commit>` |

`deploy.sh` pulls the image, backs up the database, starts the new version and waits for
`/health/`. If the new version isn't healthy within 3 minutes, it starts the previous tag
again and the GitHub job turns red. Database migrations are not undone automatically —
restore from the pre-deploy dump in `/var/backups/oxi` if a migration was the problem.

**Turn it on (once the server from steps 1–6 works):**

1. **Let the server pull images.** After the first successful `build` on GitHub, open the
   package (repository page → **Packages** → `oxi_site`) → **Package settings** →
   **Change visibility → Public**. The repository is public anyway, and the image contains no
   secrets (they live in `deploy/.env` on the server).
   For a private package instead: create a token with only `read:packages` and run
   `docker login ghcr.io -u <login>` on the server once.
2. **Point the server at the image.** In `deploy/.env` set
   `IMAGE=ghcr.io/<your-login-in-lowercase>/oxi_site`.
3. **A deploy key for GitHub Actions** (on your computer):
   ```bash
   ssh-keygen -t ed25519 -f oxi_deploy -N "" -C "github-actions-deploy"
   ssh-copy-id -i oxi_deploy.pub oxi@SERVER_IP
   ssh-keyscan SERVER_IP          # copy the output for DEPLOY_KNOWN_HOSTS
   ```
4. **Repository settings → Secrets and variables → Actions:**
   - Secrets: `DEPLOY_HOST` (server IP), `DEPLOY_USER` (`oxi`),
     `DEPLOY_SSH_KEY` (contents of the private `oxi_deploy` file),
     `DEPLOY_KNOWN_HOSTS` (output of `ssh-keyscan`).
   - Variables: `DEPLOY_ENABLED` = `true`.
   Delete the local `oxi_deploy` files afterwards; the key lives only in GitHub and on the server.
5. (Optional) **Settings → Environments → production → Required reviewers**: GitHub then asks
   you to approve each deploy before it runs.

From then on: merge `develop` → `main` and the site updates itself.

## Updating the site by hand

With CI images (any earlier tag works too, for a manual rollback):

```bash
cd ~/oxi_site && git pull
./deploy/deploy.sh sha-1a2b3c4
```

Without CI, building on the server:

```bash
cd ~/oxi_site && git pull
cd deploy && docker compose -f docker-compose.prod.yml up -d --build
```

Migrations and static files are applied automatically when `web` restarts.
Downtime is a few seconds.

## Useful commands

```bash
cd ~/oxi_site/deploy
alias dc="docker compose -f docker-compose.prod.yml"
dc ps                       # status
dc logs -f web              # app logs (also worker, caddy, db)
dc exec web python manage.py shell
dc restart web
df -h && docker system df   # disk usage
docker system prune -f      # remove old images after updates
```

## If something goes wrong

| Symptom | Check |
| --- | --- |
| Browser shows a certificate error | DNS points to the server (`dig +short yourdomain.com`), ports 80/443 open; `dc logs caddy` |
| "Bad Request (400)" | `DOMAIN` in `.env` matches the address you open; restart: `dc up -d` |
| 502 Bad Gateway | `web` isn't running: `dc logs web` (often a typo in `.env`) |
| Admin works, styles missing | `dc restart web` (re-collects static files) |
| `set … in deploy/.env` error on start | A required value is missing in `deploy/.env` |
