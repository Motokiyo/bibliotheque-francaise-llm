# Smoke Test

Run this after deployment and after every update.

## Local service

```bash
sudo systemctl status conteur.service --no-pager
curl -sS http://127.0.0.1:7860/api/status
```

Expected:

- service is `active (running)`;
- status JSON reports `has_key: true`;
- default voice is `cedar`;
- no bind on public `0.0.0.0:7860`.

Check the bind explicitly:

```bash
sudo ss -ltnp | grep 7860
```

Expected address: `127.0.0.1:7860`.

## Nginx and HTTPS

```bash
sudo nginx -t
curl -I https://conteur.eiffelai.io/
curl -I -u USER:PASS https://conteur.eiffelai.io/
```

Expected:

- without credentials: `401 Unauthorized`;
- with credentials: `200 OK`;
- certificate is valid in the browser.

## WebSocket path

Open the site from a phone on mobile data or another network:

1. Authenticate.
2. Select a book.
3. Press `Démarrer`.
4. Confirm server logs show a WebSocket session and OpenAI Realtime events.

Useful logs:

```bash
sudo journalctl -u conteur.service -f
sudo tail -f /var/log/nginx/conteur.error.log
```

## Book continuity check

For a 10 minute test:

- no browser reload;
- no `connecting` state stuck longer than a few seconds;
- no unexpected jump to the next chapter;
- several segments requested automatically;
- pause then resume continues near the saved offset;
- chapter previous/next buttons still work.
