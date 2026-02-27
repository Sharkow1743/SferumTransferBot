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
from aiogram.exceptions import TelegramBadRequest

# PyMax Imports
from pymax import SocketMaxClient, MaxClient, Message
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
    USE_SOCKET_CLIENT = os.getenv('USE_SOCKET_CLIENT', 'false')
    USE_SOCKET_CLIENT = True if USE_SOCKET_CLIENT.lower() == 'true' else False
    MAX_PHONE = os.getenv('VK_PHONE')
    MAX_CHAT_ID = int(os.getenv('VK_CHAT_ID'))
    MAX_TOKEN = os.getenv('VK_COOKIE')
    TG_CHAT_ID = os.getenv('TG_CHAT_ID')
    TG_TOKEN = os.getenv('TG_TOKEN')
    ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 0))
    if not all([MAX_CHAT_ID, TG_CHAT_ID, TG_TOKEN, MAX_TOKEN, MAX_PHONE]):
        raise ValueError("One or more environment variables are not set.")
except (ValueError, TypeError) as e:
    logging.critical(f"FATAL: Configuration error - {e}. Please check your .env file.")
    sys.exit(1)

# --- State ---
msgs_map = data_handler.load('msgs') or {}
last_sender_id = None

# --- API Initialization ---
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()

# Reconnect=True effectively replaces the "Watchdog" thread
if USE_SOCKET_CLIENT:
    client = SocketMaxClient(MAX_PHONE, token=MAX_TOKEN, work_dir="data/cache", reconnect=True)
else:
    client = MaxClient(MAX_PHONE, token=MAX_TOKEN, work_dir="data/cache", reconnect=True)


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

async def get_smart_sender_info(user_id: int):
    """Fetches name and determines gender-specific verb suffix."""
    try:
        user = await client.get_user(user_id=user_id)
        if user:
            name = f"{user.names[0].name}" if user.names else f"User {user_id}"
            # Sex: 1 is Female, 2 is Male. Default to 'л' (male/neutral)
            suffix = "ла" if user.gender == 1 else "л"
            return name, suffix
    except Exception as e:
        logging.error(f"Error fetching user {user_id}: {e}")
    return f"User {user_id}", "л(-а)"

# --- Logic: Max -> Telegram ---

async def process_max_message(message: Message, forwarded: bool = False) -> int:
    """
    Handles messages. Returns the Telegram Message ID of the first part sent.
    """
    global last_sender_id
    
    # 1. Top-level filter
    if not forwarded and message.chat_id != MAX_CHAT_ID:
        return None
    if message.text and message.text.startswith(BOT_MESSAGE_PREFIX):
        return None

    msg_id_str = str(message.id) if message.id else "FWD_PART"
    logging.info(f"Processing Max Message ID: {msg_id_str} (Forwarded: {forwarded})")

    # This will track the FIRST Telegram ID associated with this Max message
    first_tg_id = None

    try:
        sender_name, gender_suffix = await get_smart_sender_info(message.sender)
        
        # 2. Header Logic
        if not forwarded and last_sender_id != message.sender:
            header_text = f"{BOT_MESSAGE_PREFIX} *{sender_name} написа{gender_suffix}:*"
            sent_header = await bot.send_message(TG_CHAT_ID, header_text, parse_mode="Markdown")
            first_tg_id = sent_header.message_id
            last_sender_id = message.sender

        # 3. Reply Mapping (Lookup)
        reply_to_tg_id = None
        if message.link and message.link.type == 'REPLY':
            replied_max_id = str(message.link.message.id)
            reply_to_tg_id = msgs_map.get(replied_max_id)
            if reply_to_tg_id:
                logging.info(f"Reply Link: Max[{replied_max_id}] -> TG[{reply_to_tg_id}]")

        # 4. Forward Recursion
        fwds_to_process = []
        if message.link and message.link.type == 'FORWARD':
            fwds_to_process.append(message.link.message)
        if hasattr(message, 'fwd_messages') and message.fwd_messages:
            fwds_to_process.extend(message.fwd_messages)

        for fwd_msg in fwds_to_process:
            # Recursive call returns the TG ID of the forwarded message
            fwd_tg_id = await process_max_message(fwd_msg, forwarded=True)
            # If our container doesn't have a TG ID yet (no header), use the first forward's ID
            if first_tg_id is None:
                first_tg_id = fwd_tg_id

        # 5. Content Prep
        text_content = message.text or ""
        if forwarded:
            text_content = f"↪S_Переслано от {sender_name}:_\n{text_content}"
        
        # 6. Attachments
        if message.attaches:
            for attach in message.attaches:
                sent = None
                try:
                    if isinstance(attach, PhotoAttach):
                        f_bytes = await download_content(attach.base_url)
                        sent = await bot.send_photo(
                            TG_CHAT_ID,
                            photo=BufferedInputFile(f_bytes.getvalue(), filename="photo.jpg"),
                            caption=text_content if text_content else None,
                            reply_to_message_id=reply_to_tg_id,
                            parse_mode="Markdown"
                        )
                    elif isinstance(attach, VideoAttach):
                        vid_info = await client.get_video_by_id(message.chat_id, message.id, attach.video_id)
                        if vid_info and vid_info.url:
                            f_bytes = await download_content(vid_info.url)
                            sent = await bot.send_video(
                                TG_CHAT_ID,
                                video=BufferedInputFile(f_bytes.getvalue(), filename="video.mp4"),
                                caption=text_content if text_content else None,
                                reply_to_message_id=reply_to_tg_id,
                                parse_mode="Markdown"
                            )
                    elif isinstance(attach, FileAttach):
                        file_info = await client.get_file_by_id(message.chat_id, message.id, attach.file_id)
                        if file_info and file_info.url:
                            f_bytes = await download_content(file_info.url)
                            sent = await bot.send_document(
                                TG_CHAT_ID,
                                document=BufferedInputFile(f_bytes.getvalue(), filename=getattr(file_info, 'name', 'file')),
                                caption=text_content if text_content else None,
                                reply_to_message_id=reply_to_tg_id,
                                parse_mode="Markdown"
                            )

                    if sent:
                        if first_tg_id is None: first_tg_id = sent.message_id
                        text_content = "" # Only send caption once
                except Exception as e:
                    logging.error(f"Attachment error: {e}")

        # 7. Remaining Text
        if text_content.strip():
            sent_msg = await bot.send_message(
                TG_CHAT_ID,
                text_content,
                reply_to_message_id=reply_to_tg_id,
                parse_mode="Markdown"
            )
            if first_tg_id is None: first_tg_id = sent_msg.message_id

        # 8. Save Mapping
        # We save mapping for both forwarded items and top-level containers
        if first_tg_id and message.id:
            msgs_map[str(message.id)] = first_tg_id
            data_handler.save('msgs', msgs_map)
            logging.info(f"Mapping Saved: Max[{message.id}] == TG[{first_tg_id}]")

        return first_tg_id

    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        return None

@client.on_message()
async def max_message_handler(message: Message):
    # PyMax entry point
    await process_max_message(message)

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
    # Setup Signal Handling
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def stop_signal_handler():
        logging.warning("Shutdown signal received.")
        stop_event.set()

    # ONLY add signal handlers if NOT on Windows
    if os.name != 'nt': 
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_signal_handler)
    else:
        logging.info("Running on Windows: Use Ctrl+C to stop the bot.")

    # Start Max Client
    logging.info("Initializing Max Client...")
    # Note: Ensure client.start() is awaited or run as a task depending on PyMax version
    await client.start()

    # Start Telegram Poller
    logging.info("Starting Telegram Polling...")
    tg_task = asyncio.create_task(dp.start_polling(bot))

    await on_startup()
    
    try:
        # On Windows, this will wait until the program is interrupted
        # On Linux, this will also wait for the stop_event (SIGINT/SIGTERM)
        if os.name != 'nt':
            await stop_event.wait()
        else:
            # Keep the loop alive on Windows until KeyboardInterrupt
            while True:
                await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logging.warning("Manual stop triggered.")
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