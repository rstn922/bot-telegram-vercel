import os
import json
import logging
import datetime
import re
import httpx
import dateparser.search
from fastapi import FastAPI, Request
import redis as redis_lib

app = FastAPI()

# Token Telegram dari Environment Variable
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8222875265:AAHiGudrPhJzkPm2Z9mUg5G2Dfr5g7gcHwI")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

redis_url = os.getenv("REDIS_URL")
if redis_url:
    redis = redis_lib.Redis.from_url(redis_url, decode_responses=True)
else:
    redis = None

logging.basicConfig(level=logging.INFO)

async def send_telegram_message(chat_id: int, text: str):
    """Mengirim pesan ke Telegram menggunakan httpx"""
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{TELEGRAM_API_URL}/sendMessage",
                json={"chat_id": chat_id, "text": text}
            )
        except Exception as e:
            logging.error(f"Gagal mengirim pesan: {e}")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Menerima pesan (Update) dari Telegram"""
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}
        
    if "message" not in data or "text" not in data["message"]:
        return {"status": "ok"}
        
    chat_id = data["message"]["chat"]["id"]
    text = data["message"]["text"]
    
    if not redis:
        await send_telegram_message(chat_id, 'Error 500: REDIS_URL belum dikonfigurasi di Environment Variables!')
        return {"status": "ok"}
    
    # Jika pesan adalah perintah /start
    if text.startswith("/start"):
        await send_telegram_message(
            chat_id,
            'Halo! Saya bot pengingat Serverless. 🚀\n\n'
            'Ketik pesan seperti:\n'
            '• "ingatkan aku beli sabun jam 15:00"\n'
            '• "ingetin meeting besok jam 09:00"'
        )
        return {"status": "ok"}

    # Ekstrak waktu menggunakan dateparser
    settings = {
        'PREFER_DATES_FROM': 'future',
        'TIMEZONE': 'Asia/Jakarta', 
        'RETURN_AS_TIMEZONE_AWARE': True,
    }
    
    text_normalized = re.sub(r'(jam|pukul)?\s*(\d{1,2})\.(\d{2})', r'\1 \2:\3', text, flags=re.IGNORECASE).strip()
    
    try:
        extracted_dates = dateparser.search.search_dates(
            text_normalized, 
            languages=['id', 'en'], 
            settings=settings
        )
    except Exception as e:
        logging.error(f"Dateparser error: {e}")
        extracted_dates = None
        
    if not extracted_dates:
        await send_telegram_message(chat_id, 'Maaf, saya tidak dapat mendeteksi waktu dari pesan Anda.')
        return {"status": "ok"}

    time_str, dt = extracted_dates[0]
    
    if "lagi" not in text_normalized.lower():
        dt = dt.replace(second=0, microsecond=0)
        
    now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    delay = (dt - now).total_seconds()
    
    if delay <= 0:
        await send_telegram_message(chat_id, f'Waktu yang terdeteksi ({dt.strftime("%d %b %Y %H:%M")}) sudah lewat!')
        return {"status": "ok"}
        
    # Ekstrak tugas
    task = text_normalized.replace(time_str, '').strip()
    pattern = r'(?i)^(tolong\s+)?(ingatkan|ingetin|remind)(\s+aku|\s+saya|me)?(\s+untuk|\s+supaya|\s+agar|\s+buat|to)?\s*'
    task = re.sub(pattern, '', task).strip()
    task = re.sub(r'(?i)^(di|pada|jam|pukul)\s+', '', task).strip()
    
    if not task:
        task = "Sesuatu yang Anda jadwalkan"

    # SIMPAN KE REDIS
    timestamp = int(dt.timestamp())
    payload = json.dumps({"chat_id": chat_id, "task": task})
    
    redis.zadd("reminders", {payload: timestamp})
    
    formatted_time = dt.strftime("%d %B %Y - Pukul %H:%M:%S")
    await send_telegram_message(
        chat_id, 
        f'✨ Siap Serverless!\n\nSaya akan mengingatkan Anda untuk:\n👉 "{task}"\n\n⏰ Pada:\n{formatted_time}'
    )
    
    return {"status": "ok"}

@app.get("/cron")
async def run_cron(request: Request):
    if not redis:
        return {"status": "error", "message": "Redis not configured"}
        
    current_time = int(datetime.datetime.now().timestamp())
    
    # Ambil semua pengingat yang waktunya sudah tiba (Score <= current_time)
    due_reminders = redis.zrangebyscore("reminders", min=0, max=current_time)
    
    if not due_reminders:
        return {"status": "ok", "message": "Tidak ada pengingat saat ini."}
        
    for reminder_str in due_reminders:
        try:
            reminder = json.loads(reminder_str)
            chat_id = reminder["chat_id"]
            task = reminder["task"]
            
            await send_telegram_message(
                chat_id, 
                f'🚨 **PENGINGAT** 🚨\n\nWaktunya untuk:\n👉 {task}'
            )
        except Exception as e:
            logging.error(f"Gagal memproses pengingat: {e}")
            
    # Hapus pengingat yang sudah dikirim dari database
    redis.zremrangebyscore("reminders", min=0, max=current_time)
    
    return {"status": "success", "sent": len(due_reminders)}
