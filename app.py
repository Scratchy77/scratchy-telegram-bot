import os
import json
import requests
from flask import Flask, request

# === Config ===
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # set on Render dashboard
if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
app = Flask(__name__)

def send_message(chat_id: int, text: str):
    resp = requests.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=15)
    print("➡️ sendMessage status:", resp.status_code, resp.text, flush=True)

@app.get("/")
def index():
    return "Bot attivo su Render ✅"

@app.get("/healthz")
def healthz():
    return "ok"

@app.post("/webhook")
def webhook():
    data = request.get_json(force=True, silent=False)
    print("📩 Update JSON:", json.dumps(data, ensure_ascii=False), flush=True)

    message = data.get("message") or data.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text", "")

    if not chat_id:
        return "no chat", 200

    if text == "/start":
        reply = "Ciao! Sono vivo su <b>Render</b> 🚀
Prova a scrivermi qualcosa."
    else:
        reply = f"Ho ricevuto: <b>{text}</b>" if text else "Messaggio ricevuto!"

    try:
        send_message(chat_id, reply)
    except Exception as e:
        print("❗Errore send_message:", repr(e), flush=True)

    return "ok", 200

@app.get("/setwebhook")
def set_webhook():
    # Render imposta automaticamente questa variabile con l'URL pubblico del servizio
    public_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not public_url:
        return "RENDER_EXTERNAL_URL non trovato. Imposta manualmente il webhook via BotFather o usa curl.", 500

    target = public_url.rstrip("/") + "/webhook"
    resp = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": target}, timeout=15)
    return f"setWebhook -> {resp.status_code} {resp.text}\nURL: {target}", 200

@app.get("/getwebhookinfo")
def get_webhook_info():
    resp = requests.get(f"{TELEGRAM_API}/getWebhookInfo", timeout=15)
    return resp.text, 200
