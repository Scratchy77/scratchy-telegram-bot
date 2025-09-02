# Telegram Bot su Render (Flask + Webhook)

## File inclusi
- `app.py` — Flask app con endpoint `/webhook`, `/setwebhook`, `/getwebhookinfo`
- `requirements.txt` — dipendenze
- `render.yaml` — (opzionale) blueprint per Render

## Deploy rapido (GUI Render)
1. Crea un repository su GitHub e carica questi tre file.
2. Su **render.com**: New → **Web Service** → collega il repo.
3. **Environment**: Python.
4. **Build Command**: `pip install -r requirements.txt`
5. **Start Command**: `gunicorn app:app --workers 1 --threads 8 --timeout 120`
6. **Environment Variables** → aggiungi: `TELEGRAM_BOT_TOKEN = <il tuo token>`
7. Deploy.
8. Quando è *Live*, apri `https://<tuo-servizio>.onrender.com/setwebhook`.
9. Scrivi al bot su Telegram: `/start` e poi un messaggio qualsiasi.

## Troubleshooting
- Controlla i **Logs** su Render quando invii un messaggio: dovresti vedere `📩 Update JSON: ...`
- Se `/setwebhook` risponde con errore e manca `RENDER_EXTERNAL_URL`, copia l'URL pubblico del servizio e imposta il webhook con:
  ```bash
  curl -X POST "https://api.telegram.org/bot<token>/setWebhook" -d "url=https://<tuo-servizio>.onrender.com/webhook"
  ```
- Il piano free può andare in *sleep*; il primo messaggio potrebbe impiegare qualche secondo a “svegliare” il servizio.
