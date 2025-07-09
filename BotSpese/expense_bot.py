"""
Expense Tracker Bot – Multi‑database per mese (v.09‑lug‑2025)
============================================================
• Accetta vocali + testi su Telegram
• Scrive la spesa nel **database Notion corrispondente al mese corrente**
• I database sono mappati in `DB_IDS_BY_MONTH` (formato chiave "MM-YYYY")

Replit Secrets richiesti: TG_TOKEN, NOTION_TOKEN, OPENAI_API_KEY

Dipendenze: python-telegram-bot==21.0, openai==0.28, notion-client, ffmpeg-python
"""

import os, re, logging, subprocess, tempfile, string
import datetime as dt
from pathlib import Path

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from notion_client import Client as Notion
import openai

# ───────────────────────────────────────────────────────────────────────────────
# Config & Secrets
# ───────────────────────────────────────────────────────────────────────────────
TG_TOKEN       = os.getenv("TG_TOKEN")
NOTION_TOKEN   = os.getenv("NOTION_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
assert all([TG_TOKEN, NOTION_TOKEN, OPENAI_API_KEY]), "Env vars missing."

openai.api_key = OPENAI_API_KEY
notion = Notion(auth=NOTION_TOKEN)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("expense_bot")

# ───────────────────────────────────────────────────────────────────────────────
# Mappa database per mese (aggiungi qui i nuovi ID!)
# ───────────────────────────────────────────────────────────────────────────────
DB_IDS_BY_MONTH = {
    "07-2025": "22b2ddb994ba81dba631d8415085778b",  # Luglio 2025
    "08-2025": "22b2ddb994ba807ca890d78a0f67387a",  # Agosto 2025
    "09-2025": "22b2ddb994ba80838abec2185a953463",  # Settembre 2025
    # aggiungi altri qui…
}

def current_db_id():
    key = dt.datetime.today().strftime("%m-%Y")  # es. 07-2025
    return DB_IDS_BY_MONTH.get(key)

# ───────────────────────────────────────────────────────────────────────────────
# Dizionari fissi categ./metodo
# ───────────────────────────────────────────────────────────────────────────────
CATEGORY_KEYWORDS = {
    "supermercato": "Cibo", "spesa": "Cibo", "bar": "Cibo", "ristorante": "Cibo", "latte": "Cibo",
    "amazon": "Shopping", "scarpe": "Shopping", "vestiti": "Shopping",
    "cinema": "Divertimento", "concerto": "Divertimento", "biglietto": "Divertimento",
    "palestra": "Sport e salute", "farmacia": "Sport e salute",
    "treno": "Trasporti", "taxi": "Trasporti", "benzina": "Trasporti",
    "hotel": "Viaggi", "volo": "Viaggi"
}
PAYMENT_KEYWORDS = {
    "contanti": "Contanti", "cash": "Contanti",
    "bancomat": "Carta", "carta": "Carta", "credito": "Carta", "debito": "Carta",
    "paypal": "Carta", "satispay": "Carta"
}
PRICE_RE = re.compile(r"(\d+[\.,]?\d*)\s?(?:€|euro?)", re.I)

# ───────────────────────────────────────────────────────────────────────────────
# Helpers: audio → testo (Whisper) & NLP
# ───────────────────────────────────────────────────────────────────────────────

def to_mp3(ogg: Path) -> Path:
    mp3 = ogg.with_suffix(".mp3")
    subprocess.run(["ffmpeg", "-y", "-i", str(ogg), str(mp3)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return mp3

def whisper(ogg: Path) -> str:
    with to_mp3(ogg).open("rb") as f:
        res = openai.Audio.transcribe("whisper-1", f, language="it")
    return res["text"].strip()

# GPT compatta descrizione

def compact_desc(text: str) -> str:
    clean_text = text.replace("\n", " ")
    prompt = f'Testo: "{clean_text}". Oggetto acquistato (max 3 parole)?'

    try:
        r = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[{"role": "user", "content": prompt}], max_tokens=10, temperature=0)
        desc = r.choices[0].message.content.strip().strip(string.punctuation)
        return desc.capitalize() or "Spesa"
    except Exception as e:
        log.warning("GPT fallback: %s", e)
        return "Spesa"

# Parsing funzioni

def get_price(t: str):
    m = PRICE_RE.search(t)
    return float(m.group(1).replace(",", ".")) if m else None

def get_category(t: str):
    return next((v for k, v in CATEGORY_KEYWORDS.items() if k in t.lower()), "Altro")

def get_payment(t: str):
    return next((v for k, v in PAYMENT_KEYWORDS.items() if k in t.lower()), "Carta")

# ───────────────────────────────────────────────────────────────────────────────
# Core: salva su Notion
# ───────────────────────────────────────────────────────────────────────────────

def save_expense(props):
    db_id = current_db_id()
    if not db_id:
        raise Exception("Database ID non configurato per il mese corrente")
    notion.pages.create(
        parent={"database_id": db_id},
        properties={
            "Name": {"title": [{"text": {"content": props["desc"]}}]},
            "Prezzo": {"number": props["price"]},
            "Date": {"date": {"start": dt.date.today().isoformat()}},
            "Categoria": {"select": {"name": props["cat"]}},
            "Metodo di pagamento": {"select": {"name": props["pay"]}},
        },
    )
    log.info("Salvato: %s – %.2f €", props["desc"], props["price"])

# ───────────────────────────────────────────────────────────────────────────────
# Pipeline testo completo → proprietà spesa
# ───────────────────────────────────────────────────────────────────────────────

def parse_text(t: str):
    price = get_price(t)
    return {
        "price": price,
        "pay": get_payment(t),
        "cat": get_category(t),
        "desc": compact_desc(t),
    }

async def ingest(transcription: str, upd: Update):
    data = parse_text(transcription)
    if data["price"] is None:
        await upd.message.reply_text("Importo non riconosciuto ✖️")
        return
    save_expense(data)
    await upd.message.reply_text("Spesa registrata ✅")

# ───────────────────────────────────────────────────────────────────────────────
# Telegram handlers
# ───────────────────────────────────────────────────────────────────────────────
async def handle_voice(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tmp = tempfile.TemporaryDirectory()
    ogg = Path(tmp.name) / "voice.ogg"
    await (await ctx.bot.get_file(upd.message.voice.file_id)).download_to_drive(str(ogg))
    try:
        await ingest(whisper(ogg), upd)
    finally:
        tmp.cleanup()

async def handle_text(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ingest(upd.message.text, upd)

# ───────────────────────────────────────────────────────────────────────────────
# Main entry
# ───────────────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    log.info("Bot online: pronto per vocali & testi (database mensile)")
    app.run_polling()

if __name__ == "__main__":
    main()
