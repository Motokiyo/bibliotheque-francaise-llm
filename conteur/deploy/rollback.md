# Rollback

This rollback must only affect `conteur.eiffelai.io`.

## Disable public access immediately

```bash
sudo rm -f /etc/nginx/sites-enabled/conteur.eiffelai.io
sudo nginx -t
sudo systemctl reload nginx
```

## Stop the app

```bash
sudo systemctl stop conteur.service
sudo systemctl disable conteur.service
```

## Re-enable after a fix

```bash
sudo systemctl enable --now conteur.service
sudo ln -s /etc/nginx/sites-available/conteur.eiffelai.io /etc/nginx/sites-enabled/conteur.eiffelai.io
sudo nginx -t
sudo systemctl reload nginx
```

## Full cleanup if abandoning this deployment

Do this only if the service is no longer needed:

```bash
sudo systemctl stop conteur.service || true
sudo systemctl disable conteur.service || true
sudo rm -f /etc/systemd/system/conteur.service
sudo systemctl daemon-reload
sudo rm -f /etc/nginx/sites-enabled/conteur.eiffelai.io
sudo rm -f /etc/nginx/sites-available/conteur.eiffelai.io
sudo rm -f /etc/nginx/.htpasswd-conteur
sudo rm -rf /etc/conteur
sudo nginx -t
sudo systemctl reload nginx
```

The repository under `/srv/conteur` can be removed separately after confirming no
other service depends on it.
