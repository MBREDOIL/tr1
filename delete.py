import os
import re
import logging
import asyncio
import aiohttp
import aiofiles
import hashlib
import yt_dlp
from urllib.parse import urlparse, urljoin, unquote
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Document
)
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import AndTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from bs4 import BeautifulSoup
from aiofiles import os as async_os

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_MESSAGE_LENGTH = 4096
TIMEZONE = "Asia/Kolkata"
MAX_TRACKED_PER_USER = 15
SUPPORTED_EXTENSIONS = {
    'pdf': ['.pdf'],
    'image': ['.jpg', '.jpeg', '.png', '.webp'],
    'audio': ['.mp3', '.wav', '.ogg', '.m4a'],
    'video': ['.mp4', '.mkv', '.mov', '.webm']
}

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "url_tracker_bot"

# Initialize MongoDB Client
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client[DB_NAME]

class MongoDB:
    """MongoDB operations handler"""
    users = db['users']
    urls = db['tracked_urls']
    sudo = db['sudo_users']
    authorized = db['authorized_chats']

class URLTrackerBot:
    def __init__(self):
        self.app = Client(
            "url_tracker_bot",
            api_id=int(os.getenv("API_ID")),
            api_hash=os.getenv("API_HASH"),
            bot_token=os.getenv("BOT_TOKEN")
        )
        self.scheduler = AsyncIOScheduler(timezone=TIMEZONE)
        self.http = aiohttp.ClientSession()
        self.ydl_opts = {
            'format': 'best',
            'quiet': True,
            'noprogress': True,
            'nocheckcertificate': True,
            'max_filesize': MAX_FILE_SIZE,
            'outtmpl': 'downloads/%(id)s.%(ext)s'
        }
        self.initialize_handlers()
        self.create_downloads_dir()

    def create_downloads_dir(self):
        if not os.path.exists('downloads'):
            os.makedirs('downloads')

    def initialize_handlers(self):
        handlers = [
            (self.track_handler, 'track'),
            (self.untrack_handler, 'untrack'),
            (self.list_handler, 'list'),
            (self.sudo_handler, 'addsudo'),
            (self.sudo_handler, 'removesudo'),
            (self.auth_handler, 'authchat'),
            (self.auth_handler, 'unauthchat'),
            (self.documents_handler, 'documents'),
            (self.ytdl_handler, 'dl'),
            (self.start_handler, 'start'),
            (self.help_handler, 'help'),
            (CallbackQueryHandler(self.nightmode_toggle), None),
            (CallbackQueryHandler(self.delete_entry), None)
        ]
        
        for handler, command in handlers:
            if command:
                self.app.add_handler(MessageHandler(handler, filters.command(command)))

    # ------------------- Authorization ------------------- #
    async def is_authorized(self, message: Message) -> bool:
        if message.chat.type == enums.ChatType.CHANNEL:
            return await MongoDB.authorized.find_one({'chat_id': message.chat.id})
        return any([
            await MongoDB.sudo.find_one({'user_id': message.from_user.id}),
            message.from_user.id == int(os.getenv("OWNER_ID")),
            await MongoDB.authorized.find_one({'chat_id': message.chat.id})
        ])

    # ------------------- Enhanced Web Monitoring ------------------- #
    async def get_webpage_content(self, url: str) -> Tuple[str, List[Dict]]:
        try:
            async with self.http.get(url, timeout=30) as resp:
                content = await resp.text()
                soup = BeautifulSoup(content, 'lxml')
                
                resources = []
                seen_hashes = set()
                
                for tag in soup.find_all(['a', 'img', 'audio', 'video', 'source']):
                    resource_url = None
                    if tag.name == 'a' and (href := tag.get('href')):
                        resource_url = unquote(urljoin(url, href))
                    elif (src := tag.get('src')):
                        resource_url = unquote(urljoin(url, src))
                    
                    if resource_url:
                        try:
                            async with self.http.get(resource_url) as r:
                                file_content = await r.read()
                                file_hash = hashlib.md5(file_content).hexdigest()
                                if file_hash in seen_hashes:
                                    continue
                                seen_hashes.add(file_hash)
                        except:
                            file_hash = hashlib.md5(resource_url.encode()).hexdigest()
                        
                        ext = os.path.splitext(resource_url)[1].lower()
                        for file_type, extensions in SUPPORTED_EXTENSIONS.items():
                            if ext in extensions:
                                resources.append({
                                    'url': resource_url,
                                    'type': file_type,
                                    'hash': file_hash
                                })
                                break
                
                return content, resources
        except Exception as e:
            logger.error(f"Web monitoring error: {str(e)}")
            return "", []

    # ------------------- YT-DLP Enhanced Integration ------------------- #
    async def ytdl_download(self, url: str) -> Optional[str]:
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)
                
                if 'entries' in info:
                    info = info['entries'][0]

                filename = ydl.prepare_filename(info)
                if os.path.exists(filename):
                    return filename
                
                await asyncio.to_thread(ydl.download, [url])
                return filename
        except yt_dlp.utils.DownloadError as e:
            logger.error(f"YT-DLP Download Error: {str(e)}")
            return await self.direct_download(url)
        except Exception as e:
            logger.error(f"YT-DLP General Error: {str(e)}")
            return None

    async def direct_download(self, url: str) -> Optional[str]:
        try:
            async with self.http.get(url) as resp:
                if resp.status != 200:
                    return None
                
                content = await resp.read()
                if len(content) > MAX_FILE_SIZE:
                    return None
                
                file_ext = os.path.splitext(url)[1].split('?')[0][:4]
                file_name = f"downloads/{hashlib.md5(content).hexdigest()}{file_ext}"
                
                async with aiofiles.open(file_name, 'wb') as f:
                    await f.write(content)
                
                return file_name
        except Exception as e:
            logger.error(f"Direct download failed: {str(e)}")
            return None

    # ------------------- Message Handling ------------------- #
    async def safe_send_message(self, user_id: int, text: str, **kwargs):
        try:
            if len(text) <= MAX_MESSAGE_LENGTH:
                await self.app.send_message(user_id, text, **kwargs)
            else:
                parts = [text[i:i+MAX_MESSAGE_LENGTH] for i in range(0, len(text), MAX_MESSAGE_LENGTH)]
                for part in parts:
                    await self.app.send_message(user_id, part, **kwargs)
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Message sending failed: {str(e)}")

    # ------------------- Tracking Core Logic ------------------- #
    async def check_updates(self, user_id: int, url: str):
        try:
            tracked_data = await MongoDB.urls.find_one({'user_id': user_id, 'url': url})
            if not tracked_data:
                return

            current_content, new_resources = await self.get_webpage_content(url)
            previous_hash = tracked_data.get('content_hash', '')
            current_hash = hashlib.md5(current_content.encode()).hexdigest()
            
            if current_hash != previous_hash or new_resources:
                text_changes = f"🔄 Website Updated: {url}\n" + \
                             f"📅 Change detected at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                             
                await self.safe_send_message(user_id, text_changes)

                sent_hashes = []
                for resource in new_resources:
                    if resource['hash'] not in tracked_data.get('sent_hashes', []):
                        if await self.send_media(user_id, resource, tracked_data):
                            sent_hashes.append(resource['hash'])
                
                update_data = {
                    'content_hash': current_hash,
                    'last_checked': datetime.now()
                }
                
                if sent_hashes:
                    update_data['$push'] = {'sent_hashes': {'$each': sent_hashes}}
                
                await MongoDB.urls.update_one(
                    {'_id': tracked_data['_id']},
                    {'$set': update_data}
                )
                
        except Exception as e:
            logger.error(f"Update check failed for {url}: {str(e)}")
            await self.app.send_message(user_id, f"⚠️ Error checking updates for {url}")

    # ------------------- Media Sending ------------------- #
    async def send_media(self, user_id: int, resource: Dict, tracked_data: Dict) -> bool:
        try:
            caption = (
                f"📁 {tracked_data.get('name', 'Unnamed')}\n"
                f"🔗 Source: {tracked_data['url']}\n"
                f"📥 Direct URL: {resource['url']}"
            )
            
            file_path = await self.ytdl_download(resource['url'])
            if not file_path:
                file_path = await self.direct_download(resource['url'])
            
            if not file_path:
                return False

            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                logger.warning(f"File too big: {file_size} bytes")
                return False

            send_methods = {
                'pdf': self.app.send_document,
                'image': self.app.send_photo,
                'audio': self.app.send_audio,
                'video': self.app.send_video
            }
            
            method = send_methods.get(resource['type'], self.app.send_document)
            await method(
                user_id,
                file_path,
                caption=caption[:1024],
                parse_mode=enums.ParseMode.HTML
            )
            
            await async_os.remove(file_path)
            return True
            
        except Exception as e:
            logger.error(f"Media send failed: {str(e)}")
            return False

    # ------------------- Command Handlers ------------------- #
    async def track_handler(self, client: Client, message: Message):
        if not await self.is_authorized(message):
            return await message.reply("❌ Authorization failed!")

        try:
            parts = message.text.split(maxsplit=4)
            if len(parts) < 4:
                return await message.reply("Format: /track <name> <url> <interval> [night]")

            name = parts[1]
            url = parts[2]
            interval = int(parts[3])
            night_mode = len(parts) > 4 and parts[4].lower() == 'night'

            tracked_count = await MongoDB.urls.count_documents({'user_id': message.from_user.id})
            if tracked_count >= MAX_TRACKED_PER_USER:
                return await message.reply(f"❌ Tracking limit reached ({MAX_TRACKED_PER_USER} URLs)")

            content, _ = await self.get_webpage_content(url)
            if not content:
                return await message.reply("❌ Invalid URL or unable to access")

            await MongoDB.urls.update_one(
                {'user_id': message.from_user.id, 'url': url},
                {'$set': {
                    'name': name,
                    'interval': interval,
                    'night_mode': night_mode,
                    'content_hash': hashlib.md5(content.encode()).hexdigest(),
                    'sent_hashes': [],
                    'created_at': datetime.now()
                }},
                upsert=True
            )

            trigger = IntervalTrigger(minutes=interval)
            if night_mode:
                trigger = AndTrigger([
                    trigger,
                    CronTrigger(hour='6-22', timezone=TIMEZONE)
                ])

            self.scheduler.add_job(
                self.check_updates,
                trigger=trigger,
                args=[message.from_user.id, url],
                id=f"{message.from_user.id}_{hashlib.md5(url.encode()).hexdigest()}",
                max_instances=2
            )

            await message.reply(f"✅ Tracking started for {name}\nURL: {url}")

        except Exception as e:
            await message.reply(f"❌ Error: {str(e)}")

    async def start_handler(self, client: Client, message: Message):
        await message.reply(
            "🤖 URL Tracker Bot\n\n"
            "Commands:\n"
            "/track <name> <url> <interval> [night] - Start tracking\n"
            "/list - Show tracked URLs\n"
            "/help - Show help menu"
        )

    async def help_handler(self, client: Client, message: Message):
        await message.reply(
            "🆘 Help Menu\n\n"
            "• Track websites for file changes\n"
            "• Supports PDF, Images, Audio & Video\n"
            "• Automatic yt-dlp integration\n"
            "• Night mode avoids late notifications\n\n"
            "📌 Max file size: 50MB\n"
            "📌 Max tracked URLs per user: 15"
        )

    # ------------------- Lifecycle Management ------------------- #
    async def start(self):
        await self.app.start()
        self.scheduler.start()
        logger.info("Bot started successfully")
        await self.app.send_message(int(os.getenv("OWNER_ID")), "🤖 Bot Started Successfully")

    async def stop(self):
        await self.app.stop()
        await self.http.close()
        self.scheduler.shutdown()
        logger.info("Bot stopped gracefully")

if __name__ == "__main__":
    bot = URLTrackerBot()
    try:
        asyncio.run(bot.start())
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        asyncio.run(bot.stop())