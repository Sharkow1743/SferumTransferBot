# Standard Library Imports
import os
import sys
import asyncio
import logging
import signal
from datetime import datetime, time as t
from io import BytesIO

# Third-party Imports
import aiohttp
import colorama
from dotenv import load_dotenv
from colorama import Fore, Style
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile

# PyMax Imports
from pymax import MaxClient, Message
from pymax.types import FileAttach, PhotoAttach, VideoAttach

# Local Application Imports
import data_handler
import logger

# --- Initial Setup ---
logger.setup_logger()
api_logger = logging.getLogger("api_logger")
colorama.init(autoreset=True)
load_dotenv()

# --- Constants & Configuration ---
START_TIME = t(7, 0)
END_TIME = t(22, 0)
BOT_POST_MESSAGE = "" 
BOT_MESSAGE_PREFIX = "⫻"
BOT_START_MESSAGE = "" 

REQUESTS_TIMEOUT = 15

# --- Environment Variables ---
try:
    MAX_CHAT_ID = int(os.getenv('VK_CHAT_ID'))
    MAX_TOKEN = os.getenv('VK_COOKIE', '')
    TG_CHAT_ID = os.getenv('TG_CHAT_ID')
    TG_TOKEN = os.getenv('TG_TOKEN')
    ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID'))


    if not all([MAX_CHAT_ID, MAX_TOKEN, TG_CHAT_ID, TG_TOKEN, ADMIN_USER_ID]):
        raise ValueError("One or more environment variables are not set.")
except (ValueError, TypeError) as e:
    logging.critical(f"FATAL: Configuration error - {e}. Please check your .env file.")
    sys.exit(1)

# --- State ---
msgs_map = data_handler.load('msgs') or {}

# --- API Initialization ---
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# Reconnect=True effectively replaces the "Watchdog" thread
client = MaxClient(token=MAX_TOKEN, work_dir="data/cache", reconnect=True)


# --- Helper Functions ---

async def download_content(url: str) -> BytesIO:
    """Download content from URL into memory."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=REQUESTS_TIMEOUT) as response:
            response.raise_for_status()
            content = await response.read()
            file_bytes = BytesIO(content)
            # Attempt to set a name, though Telegram often overrides logic based on method
            file_bytes.name = response.headers.get("X-File-Name", "file")
            return file_bytes

async def get_sender_name(user_id: int) -> str:
    """Fetch user name via PyMax."""
    try:
        user = await client.get_user(user_id=user_id)
        if user and user.names:
            return user.names[0].name
    except Exception as e:
        logging.error(f"Could not fetch profile for ID {user_id}: {e}")
    return f"User {user_id}"

# --- Logic: Max -> Telegram ---

async def process_max_message(message: Message, forwarded: bool = False):
    """
    Recursive function to handle Max messages, attachments, and forwards.
    """
    try:
        # 1. Filter Check
        if message.chat_id != MAX_CHAT_ID:
            return
        if message.text and message.text.startswith(BOT_MESSAGE_PREFIX):
            return

        # 2. Prepare Sender Info
        sender_name = await get_sender_name(message.sender)
        

        reply_to_tg_id = None
        link_type = message.link.type
        if link_type == 'REPLY':
            replied_max_id = message.link.message.id
            reply_to_tg_id = msgs_map.get(replied_max_id)
            if reply_to_tg_id:
                logging.debug("Found TG message %s to reply to for Max message %s", reply_to_tg_id, message.id)
        elif link_type == 'FORWARD':
            forward_msg = message.link.message
            if forward_msg:
                # Recursively call to handle the forwarded message content
                process_max_message(forward_msg, True)
        
        # 4. Handle Text Formatting
        text_content = message.text or ""
        if forwarded and text_content:
            text_content = f"Переслано:\n{text_content}"
        
        header = ""
        if not forwarded:
            header = f"*{sender_name}*:"

        caption = f"{header}\n{text_content}".strip()
        
        tg_message_id = None

        # 6. Handle Attachments
        if message.attaches:
            # Grouping media is complex in async without mapped IDs, 
            # sending individually or best-effort for now similar to original logic.
            for attach in message.attaches:
                try:
                    if isinstance(attach, PhotoAttach):
                        f_bytes = await download_content(attach.base_url)
                        sent = await bot.send_photo(
                            TG_CHAT_ID,
                            photo=BufferedInputFile(f_bytes.getvalue(), filename="photo.jpg"),
                            caption=caption if caption else None,
                            parse_mode="Markdown",
                            reply_to_message_id=reply_to_tg_id
                        )
                        tg_message_id = sent.message_id
                        caption = "" # Clear caption after first attachment

                    elif isinstance(attach, VideoAttach):
                        vid_info = await client.get_video_by_id(message.chat_id, message.id, attach.video_id)
                        if vid_info and vid_info.url:
                            f_bytes = await download_content(vid_info.url)
                            sent = await bot.send_video(
                                TG_CHAT_ID,
                                video=BufferedInputFile(f_bytes.getvalue(), filename="video.mp4"),
                                caption=caption if caption else None,
                                parse_mode="Markdown",
                                reply_to_message_id=reply_to_tg_id
                            )
                            tg_message_id = sent.message_id
                            caption = ""

                    elif isinstance(attach, FileAttach):
                        file_info = await client.get_file_by_id(message.chat_id, message.id, attach.file_id)
                        if file_info and file_info.url:
                            f_bytes = await download_content(file_info.url)
                            sent = await bot.send_document(
                                TG_CHAT_ID,
                                document=BufferedInputFile(f_bytes.getvalue(), filename=getattr(file_info, 'name', 'doc')),
                                caption=caption if caption else None,
                                parse_mode="Markdown",
                                reply_to_message_id=reply_to_tg_id
                            )
                            tg_message_id = sent.message_id
                            caption = ""

                except Exception as e:
                    logging.error(f"Failed to process attachment for msg {message.id}: {e}")

        # 7. Send Text (if no attachments carried the caption)
        if caption:
            sent = await bot.send_message(
                TG_CHAT_ID, 
                caption, 
                parse_mode="Markdown", 
                reply_to_message_id=reply_to_tg_id
            )
            tg_message_id = sent.message_id

        # 8. Map IDs
        if tg_message_id and message.id:
            msgs_map[str(message.id)] = tg_message_id
            logging.debug(f"Mapped Max {message.id} -> TG {tg_message_id}")

    except Exception as e:
        logging.error(f"Error in process_max_message: {e}", exc_info=True)

@client.on_message()
async def max_message_handler(message: Message):
    # PyMax entry point
    await process_max_message(message)

# Note: PyMax might not have explicit events for EDITED/REMOVED in the high-level handler 
# depending on version. If needed, one observes raw events, but standard handler handles new messages.

# --- Logic: Telegram -> Max ---

@dp.message(Command("send"))
async def send_handler(msg: types.Message):
    """Handles /send command."""
    try:
        # Check time
        now = datetime.now().time()
        if msg.from_user.id != ADMIN_USER_ID and not (START_TIME <= now <= END_TIME):
            await msg.reply(f"Можно отправлять сообщения только между {START_TIME:%H:%M} и {END_TIME:%H:%M}")
            return
        
        # Check empty message
        text_to_send = msg.text.replace("/send", "", 1).strip()
        if not text_to_send:
            await msg.reply("Нельзя отправить пустое сообщение.")
            return
        
        # Get username
        username = msg.from_user.full_name or msg.from_user.username
        
        # Create full text
        full_text = f"{BOT_MESSAGE_PREFIX} *{username} написал(-а):*\n{text_to_send}"
        if BOT_POST_MESSAGE:
            full_text += f"\n{BOT_MESSAGE_PREFIX} {BOT_POST_MESSAGE}"

        # Get id of replied message in MAX
        reply_to_max_id = None
        if msg.reply_to_message:
            tg_reply_id = msg.reply_to_message.message_id
            # Reverse lookup
            for mid, tid in msgs_map.items():
                if tid == tg_reply_id:
                    reply_to_max_id = mid
                    break

        # Send message
        sent_msg = await client.send_message(
            chat_id=MAX_CHAT_ID, 
            text=full_text, 
            reply_to=reply_to_max_id 
        )

        # Map message
        if sent_msg and sent_msg.id:
            msgs_map[str(sent_msg.id)] = msg.message_id
            await msg.reply("Отправлено!")

    except Exception as e:
        logging.error(f"Error in send_handler: {e}", exc_info=True)
        await msg.reply('Произошла ошибка при отправке.')

# --- Lifecycle ---

async def on_startup():
    logging.info("Bot started. Transfer is active.")
    
    # Send startup message (invite link) logic
    if BOT_START_MESSAGE and not data_handler.load("started"):
        try:
            invite = await bot.create_chat_invite_link(TG_CHAT_ID)
            msg = BOT_START_MESSAGE.replace("TG_CHAT_INVITE_LINK", invite.invite_link)
            await client.send_message(MAX_CHAT_ID, msg)
            data_handler.save("started", True)
        except Exception as e:
            logging.error(f"Failed to send startup message: {e}")

async def main():
    # Setup Signal Handling for Docker
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def stop_signal_handler():
        logging.warning("Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_signal_handler)

    # Start Max Client
    # PyMax 1.x/2.x: start() usually initializes the polling loop. 
    # We run it as a task so we can also run the Telegram poller.
    logging.info("Initializing Max Client...")
    await client.start()

    # Start Telegram Poller
    logging.info("Starting Telegram Polling...")
    tg_task = asyncio.create_task(dp.start_polling(bot))

    await on_startup()
    
    # Keep running until signal
    try:
        await stop_event.wait()
    finally:
        logging.info("Shutting down...")
        
        # Save data
        data_handler.save('msgs', msgs_map)
        logging.info("Message map saved.")
        
        # Stop Telegram
        tg_task.cancel()
        try:
            await tg_task
        except asyncio.CancelledError:
            pass
        
        # Stop Max
        await client.close()
        await bot.session.close()
        logging.info("Shutdown complete.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")