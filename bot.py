import logging
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
import requests
import hashlib
import json
from apscheduler.schedulers.background import BackgroundScheduler

# सेटअप लॉगिंग
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(name)

USER_DATA_FILE = 'user_data.json'

def load_user_data():
    try:
        with open(USER_DATA_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_user_data(user_data):
    with open(USER_DATA_FILE, 'w') as f:
        json.dump(user_data, f)

def fetch_url_content(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

def check_website_changes(url, previous_hash):
    current_content = fetch_url_content(url)
    if current_content:
        current_hash = hashlib.sha256(current_content.encode()).hexdigest()
        return current_hash != previous_hash, current_hash
    return False, previous_hash

def check_urls(client):
    user_data = load_user_data()
    for user_id, data in user_data.items():
        for url_info in data['tracked_urls']:
            url = url_info['url']
            changed, new_hash = check_website_changes(url, url_info['hash'])
            if changed:
                client.send_message(
                    chat_id=user_id,
                    text=f"🚨 वेबसाइट में बदलाव आया है! {url}"
                )
                url_info['hash'] = new_hash
    save_user_data(user_data)

async def start(client, message):
    await message.reply_text(
        'वेबसाइट ट्रैकिंग बॉट में आपका स्वागत है!\n\n'
        'कमांड्स:\n'
        '/track <url> - वेबसाइट ट्रैक करें\n'
        '/untrack <url> - ट्रैकिंग रोकें\n'
        '/list - ट्रैक की गई वेबसाइट्स देखें'
    )

async def track(client, message):
    user_id = str(message.from_user.id)
    url = ' '.join(message.command[1:]).strip()

    if not url.startswith(('http://', 'https://')):
        await message.reply_text("⚠ कृपया वैध URL डालें (http/https के साथ)")
        return

    user_data = load_user_data()
    if user_id not in user_data:
        user_data[user_id] = {'tracked_urls': []}

    if any(u['url'] == url for u in user_data[user_id]['tracked_urls']):
        await message.reply_text("❌ यह URL पहले से ट्रैक किया जा रहा है")
        return

    content = fetch_url_content(url)
    if not content:
        await message.reply_text("❌ URL एक्सेस नहीं किया जा सका")
        return

    new_hash = hashlib.sha256(content.encode()).hexdigest()
    user_data[user_id]['tracked_urls'].append({
        'url': url,
        'hash': new_hash
    })
    save_user_data(user_data)
    await message.reply_text(f"✅ ट्रैकिंग शुरू: {url}")

async def untrack(client, message):
    user_id = str(message.from_user.id)
    url = ' '.join(message.command[1:]).strip()

    user_data = load_user_data()
    if user_id not in user_data:
        await message.reply_text("❌ कोई ट्रैक किए गए URL नहीं मिले")
        return

    original_count = len(user_data[user_id]['tracked_urls'])
    user_data[user_id]['tracked_urls'] = [
        u for u in user_data[user_id]['tracked_urls']
        if u['url'] != url
    ]

    if len(user_data[user_id]['tracked_urls']) < original_count:
        save_user_data(user_data)
        await message.reply_text(f"❎ ट्रैकिंग बंद: {url}")
    else:
        await message.reply_text("❌ URL नहीं मिला")

async def list_urls(client, message):
    user_id = str(message.from_user.id)
    user_data = load_user_data()

    if user_id not in user_data or not user_data[user_id]['tracked_urls']:
        await message.reply_text("📭 आपने अभी कोई URL ट्रैक नहीं किया है")
        return

    urls = "\n".join([u['url'] for u in user_data[user_id]['tracked_urls']])
    await message.reply_text(f"📜 ट्रैक किए गए URLs:\n\n{urls}")

def main():
    app = Client("my_bot", api_id="YOUR_API_ID", api_hash="YOUR_API_HASH", bot_token="YOUR_BOT_TOKEN")

    app.add_handler(MessageHandler(start, filters.command("start")))
    app.add_handler(MessageHandler(track, filters.command("track")))
    app.add_handler(MessageHandler(untrack, filters.command("untrack")))
    app.add_handler(MessageHandler(list_urls, filters.command("list")))

    scheduler = BackgroundScheduler()
    scheduler.add_job(check_urls, 'interval', minutes=5, args=[app])
    scheduler.start()

    try:
        app.run()
    except Exception as e:
        logger.error(f"Error running the bot: {e}")

if name == 'main':
    main()
