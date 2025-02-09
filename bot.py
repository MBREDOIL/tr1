import logging
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
import requests
from bs4 import BeautifulSoup
import hashlib
import json
from apscheduler.schedulers.background import BackgroundScheduler
import time

# सेटअप लॉगिंग
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# यूजर डेटा स्टोरेज (सरल JSON फ़ाइल-आधारित)
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

# वेबपेज कंटेंट फेच करने की फंक्शन
def fetch_url_content(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None

# वेबपेज चेंज डिटेक्शन फंक्शन
def check_website_changes(url, previous_hash):
    current_content = fetch_url_content(url)
    if current_content:
        current_hash = hashlib.sha256(current_content.encode()).hexdigest()
        return current_hash != previous_hash, current_hash
    return False, previous_hash

# शेड्यूल्ड जॉब
def check_urls(context: CallbackContext):
    user_data = load_user_data()
    for user_id, data in user_data.items():
        for url_info in data['tracked_urls']:
            url = url_info['url']
            changed, new_hash = check_website_changes(url, url_info['hash'])
            if changed:
                context.bot.send_message(
                    chat_id=user_id,
                    text=f"🚨 वेबसाइट में बदलाव आया है! {url}"
                )
                url_info['hash'] = new_hash
    save_user_data(user_data)

# टेलीग्राम बॉट कमांड हैंडलर्स
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        'वेबसाइट ट्रैकिंग बॉट में आपका स्वागत है!\n\n'
        'कमांड्स:\n'
        '/track <url> - वेबसाइट ट्रैक करें\n'
        '/untrack <url> - ट्रैकिंग रोकें\n'
        '/list - ट्रैक की गई वेबसाइट्स देखें'
    )

def track(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    url = ' '.join(context.args).strip()
    
    if not url.startswith(('http://', 'https://')):
        update.message.reply_text("⚠ कृपया वैध URL डालें (http/https के साथ)")
        return
    
    user_data = load_user_data()
    if user_id not in user_data:
        user_data[user_id] = {'tracked_urls': []}
    
    # डुप्लिकेट चेक
    if any(u['url'] == url for u in user_data[user_id]['tracked_urls']):
        update.message.reply_text("❌ यह URL पहले से ट्रैक किया जा रहा है")
        return
    
    # प्रारंभिक हैश प्राप्त करें
    content = fetch_url_content(url)
    if not content:
        update.message.reply_text("❌ URL एक्सेस नहीं किया जा सका")
        return
    
    new_hash = hashlib.sha256(content.encode()).hexdigest()
    user_data[user_id]['tracked_urls'].append({
        'url': url,
        'hash': new_hash
    })
    save_user_data(user_data)
    update.message.reply_text(f"✅ ट्रैकिंग शुरू: {url}")

def untrack(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    url = ' '.join(context.args).strip()
    
    user_data = load_user_data()
    if user_id not in user_data:
        update.message.reply_text("❌ कोई ट्रैक किए गए URL नहीं मिले")
        return
    
    # URL हटाएं
    original_count = len(user_data[user_id]['tracked_urls'])
    user_data[user_id]['tracked_urls'] = [
        u for u in user_data[user_id]['tracked_urls'] 
        if u['url'] != url
    ]
    
    if len(user_data[user_id]['tracked_urls']) < original_count:
        save_user_data(user_data)
        update.message.reply_text(f"❎ ट्रैकिंग बंद: {url}")
    else:
        update.message.reply_text("❌ URL नहीं मिला")

def list_urls(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    user_data = load_user_data()
    
    if user_id not in user_data or not user_data[user_id]['tracked_urls']:
        update.message.reply_text("📭 आपने अभी कोई URL ट्रैक नहीं किया है")
        return
    
    urls = "\n".join([u['url'] for u in user_data[user_id]['tracked_urls']])
    update.message.reply_text(f"📜 ट्रैक किए गए URLs:\n\n{urls}")

def main():
    # बॉट टोकन के साथ अपडेटर इनिशियलाइज़ करें
    updater = Updater(token="YOUR_TELEGRAM_BOT_TOKEN", use_context=True)
    
    # कमांड हैंडलर्स रजिस्टर करें
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("track", track))
    dp.add_handler(CommandHandler("untrack", untrack))
    dp.add_handler(CommandHandler("list", list_urls))
    
    # शेड्यूलर सेटअप (हर 5 मिनट में चेक)
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_urls, 'interval', minutes=5, args=[updater])
    scheduler.start()
    
    # बॉट स्टार्ट करें
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
