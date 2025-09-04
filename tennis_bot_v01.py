import os
import json
import requests
import asyncio
import httpx
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from flask import Flask, request
from dateutil import parser as dateparser
from apscheduler.schedulers.background import BackgroundScheduler
import logging

# === Config ===
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")

TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}"
app = Flask(__name__)

# Configurazione bot tennis
DEFAULT_TZ = "Europe/Rome"
CHECK_INTERVAL_MIN = 60
REMINDER_MINUTES_BEFORE = 20
DATA_DIR = os.environ.get("BOT_DATA_DIR", ".botdata")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tennis-bot")

# === Storage ===
class Storage:
    def __init__(self, users_path: str, state_path: str):
        self.users_path = users_path
        self.state_path = state_path
        self._users = self._load(users_path) or {}
        self._state = self._load(state_path) or {"known_matches": {}}

    def _load(self, path: str):
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save(self):
        try:
            with open(self.users_path, "w", encoding="utf-8") as f:
                json.dump(self._users, f, ensure_ascii=False, indent=2)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Errore salvataggio: {e}")

    def ensure_user(self, chat_id: str):
        if str(chat_id) not in self._users:
            self._users[str(chat_id)] = {
                "players": ["Jannik Sinner", "Jasmine Paolini"],
                "tz": DEFAULT_TZ,
            }
            if "known_matches" not in self._state:
                self._state["known_matches"] = {}
            self._state["known_matches"][str(chat_id)] = {}
            self.save()

    def get_players(self, chat_id: str) -> List[str]:
        return self._users.get(str(chat_id), {}).get("players", [])

    def add_player(self, chat_id: str, name: str):
        self.ensure_user(chat_id)
        u = self._users[str(chat_id)]
        if name not in u["players"]:
            u["players"].append(name)
            self.save()

    def remove_player(self, chat_id: str, name: str) -> bool:
        if str(chat_id) in self._users:
            u = self._users[str(chat_id)]
            if name in u["players"]:
                u["players"].remove(name)
                self.save()
                return True
        return False

    def get_known(self, chat_id: str) -> Dict[str, str]:
        return self._state.get("known_matches", {}).get(str(chat_id), {})

    def set_known(self, chat_id: str, match_id: str, scheduled_iso: str):
        if "known_matches" not in self._state:
            self._state["known_matches"] = {}
        if str(chat_id) not in self._state["known_matches"]:
            self._state["known_matches"][str(chat_id)] = {}
        self._state["known_matches"][str(chat_id)][match_id] = scheduled_iso
        self.save()

    def get_all_users(self) -> List[str]:
        return list(self._users.keys())

storage = Storage(USERS_FILE, STATE_FILE)

# === Tennis API ===
class SportDevsProvider:
    BASE_URL = "https://api.sportdevs.com"
    API_KEY = "ZoWU7cjlZkeNMgSvQaQ3mg"

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=20)

    async def _get(self, path: str, params: Dict[str, str] | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.API_KEY}"}
        url = f"{self.BASE_URL}{path}"
        try:
            r = await self._client.get(url, headers=headers, params=params)
            if r.status_code >= 400:
                logger.error(f"HTTP {r.status_code} su {url}: {r.text[:200]}")
                return {}
            return r.json()
        except Exception as e:
            logger.error(f"Errore API: {e}")
            return {}

    async def search_player(self, name: str) -> Optional[int]:
        try:
            data = await self._get("/tennis/players/search", params={"name": name})
            results = data.get("data") or data
            if not results:
                return None
            best = results[0]
            return int(best.get("id") or best.get("playerId"))
        except Exception as e:
            logger.error("search_player errore: %s", e)
            return None

    async def get_upcoming_matches(self, player_id: int) -> List[dict]:
        try:
            data = await self._get(f"/tennis/players/{player_id}/matches", params={"status": "scheduled"})
            raw = data.get("data") or data
            matches = []
            for m in raw[:4]:
                match_id = str(m.get("id") or m.get("matchId"))
                tname = (m.get("tournament") or {}).get("name") or m.get("competitionName")
                rnd = m.get("round") or m.get("stageName")
                p1 = m.get("home") or m.get("player1")
                p2 = m.get("away") or m.get("player2")
                sched_raw = m.get("startTime") or m.get("scheduled") or m.get("startAt")
                court = m.get("court") or (m.get("venue") or {}).get("name")
                
                dt = None
                if sched_raw:
                    try:
                        dt = dateparser.parse(sched_raw).astimezone(timezone.utc)
                    except:
                        pass
                
                matches.append({
                    "match_id": match_id,
                    "tournament": tname,
                    "round": rnd,
                    "player1": str(p1),
                    "player2": str(p2),
                    "scheduled_utc": dt,
                    "court": court,
                })
            return matches
        except Exception as e:
            logger.error(f"get_upcoming_matches errore: {e}")
            return []

provider = SportDevsProvider()

# === Telegram Functions ===
def send_message(chat_id: int, text: str):
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage", 
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, 
            timeout=15
        )
        logger.info(f"➡️ sendMessage to {chat_id}: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Errore invio messaggio: {e}")
        return False

def format_match_message(match: dict, is_new_schedule: bool = False) -> str:
    """Formatta il messaggio per una partita"""
    prefix = "🆕 NUOVO ORARIO!" if is_new_schedule else "🎾 PARTITA PROGRAMMATA"
    
    tournament = match.get("tournament", "Torneo sconosciuto")
    round_name = match.get("round", "")
    player1 = match.get("player1", "")
    player2 = match.get("player2", "")
    court = match.get("court", "")
    
    msg = f"{prefix}\n\n"
    msg += f"🏆 <b>{tournament}</b>\n"
    if round_name:
        msg += f"📋 {round_name}\n"
    msg += f"👥 {player1} vs {player2}\n"
    
    if match.get("scheduled_utc"):
        # Converti in orario italiano
        local_time = match["scheduled_utc"].astimezone(timezone(timedelta(hours=1)))
        msg += f"⏰ {local_time.strftime('%d/%m/%Y alle %H:%M')} (ora italiana)\n"
    
    if court:
        msg += f"🏟️ Campo: {court}\n"
    
    return msg

# === Background Check ===
async def check_matches_for_all_users():
    """Controlla le partite per tutti gli utenti registrati"""
    logger.info("🔍 Controllo partite per tutti gli utenti...")
    
    for chat_id in storage.get_all_users():
        try:
            await check_matches_for_user(chat_id)
        except Exception as e:
            logger.error(f"Errore controllo per utente {chat_id}: {e}")

async def check_matches_for_user(chat_id: str):
    """Controlla le partite per un singolo utente"""
    players = storage.get_players(chat_id)
    known_matches = storage.get_known(chat_id)
    
    for player_name in players:
        try:
            player_id = await provider.search_player(player_name)
            if not player_id:
                continue
                
            matches = await provider.get_upcoming_matches(player_id)
            
            for match in matches:
                match_id = match["match_id"]
                current_schedule = match["scheduled_utc"].isoformat() if match["scheduled_utc"] else None
                
                if match_id not in known_matches:
                    # Nuova partita trovata
                    if current_schedule:
                        msg = format_match_message(match, is_new_schedule=False)
                        send_message(int(chat_id), msg)
                        storage.set_known(chat_id, match_id, current_schedule)
                        logger.info(f"📤 Nuova partita inviata a {chat_id}")
                
                elif current_schedule and known_matches[match_id] != current_schedule:
                    # Orario cambiato
                    msg = format_match_message(match, is_new_schedule=True)
                    send_message(int(chat_id), msg)
                    storage.set_known(chat_id, match_id, current_schedule)
                    logger.info(f"📤 Orario aggiornato inviato a {chat_id}")
                    
        except Exception as e:
            logger.error(f"Errore controllo giocatore {player_name}: {e}")

def run_background_check():
    """Funzione sincrona per lo scheduler"""
    try:
        asyncio.run(check_matches_for_all_users())
    except Exception as e:
        logger.error(f"Errore background check: {e}")

# === Flask Routes ===
@app.get("/")
def index():
    return "🎾 Tennis Bot attivo su Render ✅"

@app.get("/healthz")
def healthz():
    return "ok"

@app.post("/webhook")
def webhook():
    try:
        data = request.get_json(force=True, silent=False)
        logger.info("📩 Update ricevuto")

        message = data.get("message") or data.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text", "").strip()

        if not chat_id:
            return "no chat", 200

        # Assicurati che l'utente sia registrato
        storage.ensure_user(str(chat_id))

        # Gestisci i comandi
        if text == "/start":
            players = storage.get_players(str(chat_id))
            reply = f"🎾 <b>Tennis Bot attivo!</b>\n\n"
            reply += f"Sto monitorando: <b>{', '.join(players)}</b>\n\n"
            reply += "Comandi disponibili:\n"
            reply += "/players - Vedi giocatori monitorati\n"
            reply += "/add [nome] - Aggiungi giocatore\n"
            reply += "/remove [nome] - Rimuovi giocatore\n"
            reply += "/check - Controlla partite ora\n"
            reply += "/status - Stato del bot"
            
        elif text == "/players":
            players = storage.get_players(str(chat_id))
            reply = f"👥 <b>Giocatori monitorati:</b>\n" + "\n".join(f"• {p}" for p in players)
            
        elif text.startswith("/add "):
            player_name = text[5:].strip()
            if player_name:
                storage.add_player(str(chat_id), player_name)
                reply = f"✅ Aggiunto <b>{player_name}</b> alla lista!"
            else:
                reply = "❌ Specifica il nome del giocatore: /add Nome Cognome"
                
        elif text.startswith("/remove "):
            player_name = text[8:].strip()
            if storage.remove_player(str(chat_id), player_name):
                reply = f"✅ Rimosso <b>{player_name}</b> dalla lista!"
            else:
                reply = f"❌ <b>{player_name}</b> non trovato nella lista"
                
        elif text == "/check":
            reply = "🔍 Controllo partite in corso..."
            send_message(chat_id, reply)
            
            # Esegui controllo asincrono
            asyncio.run(check_matches_for_user(str(chat_id)))
            reply = "✅ Controllo completato!"
            
        elif text == "/status":
            players = storage.get_players(str(chat_id))
            reply = f"🤖 <b>Stato Bot Tennis</b>\n\n"
            reply += f"👥 Giocatori: {len(players)}\n"
            reply += f"⏱️ Controllo ogni: {CHECK_INTERVAL_MIN} min\n"
            reply += f"🔔 Promemoria: {REMINDER_MINUTES_BEFORE} min prima\n"
            reply += f"🕐 Ultimo controllo: {datetime.now().strftime('%H:%M')}"
            
        else:
            reply = f"Ho ricevuto: <b>{text}</b>\n\nUsa /start per vedere i comandi disponibili."

        send_message(chat_id, reply)
        
    except Exception as e:
        logger.error(f"Errore webhook: {e}")
        
    return "ok", 200

@app.get("/setwebhook")
def set_webhook():
    public_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not public_url:
        return "RENDER_EXTERNAL_URL non trovato", 500

    target = public_url.rstrip("/") + "/webhook"
    resp = requests.post(f"{TELEGRAM_API}/setWebhook", json={"url": target}, timeout=15)
    return f"setWebhook -> {resp.status_code} {resp.text}\nURL: {target}", 200

@app.get("/getwebhookinfo")
def get_webhook_info():
    resp = requests.get(f"{TELEGRAM_API}/getWebhookInfo", timeout=15)
    return resp.text, 200

@app.get("/force-check")
def force_check():
    """Endpoint per forzare il controllo delle partite"""
    try:
        run_background_check()
        return "✅ Controllo forzato completato", 200
    except Exception as e:
        return f"❌ Errore: {e}", 500

# === Scheduler ===
scheduler = BackgroundScheduler(timezone=timezone.utc)

def start_scheduler():
    """Avvia lo scheduler per i controlli automatici"""
    scheduler.add_job(
        func=run_background_check,
        trigger="interval",
        minutes=CHECK_INTERVAL_MIN,
        id="check_matches",
        replace_existing=True
    )
    scheduler.start()
    logger.info(f"🕐 Scheduler avviato: controllo ogni {CHECK_INTERVAL_MIN} minuti")

# Avvia lo scheduler quando l'app parte
start_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
