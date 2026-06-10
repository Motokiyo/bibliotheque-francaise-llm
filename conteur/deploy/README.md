# Deploying Conteur on Hetzner

Target: `conteur.eiffelai.io`

This directory contains deployment templates only. Do not run anything here blindly on
the live server. The intended production shape is:

```text
Internet
  -> Nginx 443 HTTPS/WSS + Basic Auth
  -> 127.0.0.1:7860
  -> systemd service running Uvicorn as a dedicated non-root user
```

On the current Hetzner host, Nginx must bind HTTPS to `89.167.3.104:443`
explicitly because Tailscale also owns port 443 on its own interfaces.

## Security principles

- The app must bind to `127.0.0.1`, never `0.0.0.0`, in production.
- Only Nginx is public.
- The Linux user running the app must not be root and should own only the app
  directory and cache/log directories it needs.
- Secrets live in `/etc/conteur/conteur.env`, mode `0600`, owner `root:conteur`.
- Basic Auth is required before reaching the app.
- The production WebSocket origin allowlist must contain only
  `https://conteur.eiffelai.io`.
- Robot control and the legacy HTTP TTS endpoint stay disabled in production
  unless deliberately re-enabled.
- No secret is committed to Git.
- Rollback must be possible by disabling only this vhost and service.

## Files

- `conteur.env.example`: env file skeleton for `/etc/conteur/conteur.env`.
- `conteur.service`: systemd unit template.
- `nginx-conteur.eiffelai.io.conf`: Nginx reverse proxy with WebSocket headers.
- `rollback.md`: precise rollback procedure.
- `smoke-test.md`: checks to run after staging or production deployment.

## Suggested server layout

```text
/srv/conteur/Bibliotheque-LLM-FR/conteur
/etc/conteur/conteur.env
/etc/nginx/sites-available/conteur.eiffelai.io
/etc/nginx/sites-enabled/conteur.eiffelai.io
/etc/nginx/.htpasswd-conteur
```

## Manual deployment outline

Run these steps only during a maintenance window, after reviewing the templates.

1. Create a restricted user and directories:

```bash
sudo adduser --system --group --home /srv/conteur conteur
sudo install -d -o conteur -g conteur /srv/conteur
sudo install -d -o root -g conteur -m 0750 /etc/conteur
```

2. Put the repository under `/srv/conteur/Bibliotheque-LLM-FR`, then make the
   app user the owner of that copy:

```bash
sudo chown -R conteur:conteur /srv/conteur/Bibliotheque-LLM-FR
sudo -u conteur install -d /srv/conteur/Bibliotheque-LLM-FR/conteur/.cache
```

3. Create the virtualenv as the `conteur` user:

```bash
cd /srv/conteur/Bibliotheque-LLM-FR/conteur
sudo -u conteur python3 -m venv .venv
sudo -u conteur .venv/bin/pip install --upgrade pip
sudo -u conteur .venv/bin/pip install -r requirements.txt
```

4. Copy `conteur.env.example` to `/etc/conteur/conteur.env`, fill the real values,
   then lock permissions:

```bash
sudo install -o root -g conteur -m 0600 conteur/deploy/conteur.env.example /etc/conteur/conteur.env
sudoedit /etc/conteur/conteur.env
```

5. Install the systemd unit:

```bash
sudo install -o root -g root -m 0644 conteur/deploy/conteur.service /etc/systemd/system/conteur.service
sudo systemctl daemon-reload
sudo systemctl enable --now conteur.service
sudo systemctl status conteur.service
```

6. Create the Basic Auth file:

```bash
sudo apt-get install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd-conteur alexandre
sudo chown root:www-data /etc/nginx/.htpasswd-conteur
sudo chmod 0640 /etc/nginx/.htpasswd-conteur
```

7. Install the temporary HTTP-only Nginx vhost after DNS points
   `conteur.eiffelai.io` at the server:

```bash
sudo install -o root -g root -m 0644 conteur/deploy/nginx-conteur.eiffelai.io.bootstrap.conf /etc/nginx/sites-available/conteur.eiffelai.io
sudo ln -s /etc/nginx/sites-available/conteur.eiffelai.io /etc/nginx/sites-enabled/conteur.eiffelai.io
sudo nginx -t
sudo systemctl reload nginx
```

8. Issue the HTTPS certificate:

```bash
sudo certbot certonly --webroot -w /var/www/html -d conteur.eiffelai.io
```

9. Replace the bootstrap vhost with the final HTTPS/WSS reverse proxy:

```bash
sudo install -o root -g root -m 0644 conteur/deploy/nginx-conteur.eiffelai.io.conf /etc/nginx/sites-available/conteur.eiffelai.io
sudo nginx -t
sudo systemctl reload nginx
```

10. Run `smoke-test.md`.
