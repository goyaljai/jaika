# Jaika production deployment

Current production URL:

```text
https://187-127-151-46.sslip.io:5244/
```

The VPS keeps Jaika isolated from the existing Caddy/voice-copilot setup:

- public TLS nginx listener: `5244`
- Jaika gunicorn backend: `127.0.0.1:5245`
- systemd service: `jaika_jaika.service`
- app directory: `/opt/jaika`
- runtime secrets: `/opt/jaika/.env`

Install/update the checked-in service files:

```bash
sudo cp deployment/jaika_jaika.service /etc/systemd/system/jaika_jaika.service
sudo cp deployment/nginx_jaika_5244.conf /etc/nginx/sites-available/jaika_jaika
sudo ln -sf /etc/nginx/sites-available/jaika_jaika /etc/nginx/sites-enabled/jaika_jaika
sudo systemctl daemon-reload
sudo nginx -t
sudo systemctl enable --now nginx jaika_jaika.service
sudo systemctl restart jaika_jaika.service nginx
```

Secrets policy:

- Do not commit `.env`, refresh tokens, API keys, or `data/`.
- Use `.env.example` for placeholder variable names only.
- Store live secrets only in `/opt/jaika/.env` on the VPS.
- If a secret is accidentally committed, remove it from the repo and rotate it.

Required/optional runtime keys:

- Antigravity OAuth uses the built-in public installed-app client ID; keep the matching `ANTIGRAVITY_OAUTH_CLIENT_SECRET` only in `/opt/jaika/.env`.
- `SERP_API_KEY` enables web grounding.
- `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` enable ElevenLabs TTS.
- `GEMINI_API_KEY_1..3` enable Gemini API fallback TTS/video paths and are rotated on quota errors.
