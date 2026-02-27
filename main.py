# Standard Library Imports
import os
import sys
import threading
import logging
from datetime import datetime, time as t
import json
import time
import signal

# Third-party Imports
import telebot
import requests
import colorama
from dotenv import load_dotenv
from telebot import types
from colorama import Fore, Style
import MaxBridge

# Local Application Imports
import data_handler
import logger

# --- Initial Setup ---
logger.setup_logger()
api_logger = logging.getLogger("api_logger")
colorama.init(autoreset=True)
load_dotenv()

# --- Constants & Configuration ---
# Time window during which messages can be sent from Telegram to Max
START_TIME = t(7, 0)
END_TIME = t(22, 0)

# Health Check and Restart Configuration
HEALTH_CHECK_INTERVAL = 300  # Check API health every 5 minutes (300 seconds)
MAX_API_FAILURE_THRESHOLD = 3  # Restart bot after 3 consecutive failed health checks

REQUESTS_TIMEOUT = 15 # Timeout in seconds for downloading attachments

BOT_POST_MESSAGE = "" # What bot adds to end of message when sending to max
BOT_MESSAGE_PREFIX = "⫻" # What bot ads to the start of the message when sending to max. Also helps identifying bot`s messages, so don`t leave this empty
BOT_START_MESSAGE = f"" # What bot sends to max with first launch

# --- Environment Variables & Validation ---
try:
    MAX_CHAT_ID = int(os.getenv('VK_CHAT_ID'))
    TG_CHAT_ID = os.getenv('TG_CHAT_ID')
    TG_TOKEN = os.getenv('TG_TOKEN')
    MAX_AUTH_TOKEN = os.getenv('VK_COOKIE')
    ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID')) if os.getenv('ADMIN_USER_ID') else 0
    if not all([MAX_CHAT_ID, TG_CHAT_ID, TG_TOKEN, MAX_AUTH_TOKEN]):
        raise ValueError("One or more environment variables are not set.")
except (ValueError, TypeError) as e:
    logging.critical(f"FATAL: Configuration error - {e}. Please check your .env file.")
    sys.exit(1)

# --- State and Globals ---
msgs_map = data_handler.load('msgs') or {}
user_cache = {}
last_message_sender = None
max_api_failures = 0 # Counter for consecutive API health check failures
shutdown_event = threading.Event() # Used to signal threads to stop gracefully

# --- API Initialization ---
bot = telebot.TeleBot(TG_TOKEN, threaded=False)
api = None

# --- Core Logic: Max -> Telegram ---

def get_sender_profile(sender_id: int) -> dict:
    """
    Fetches a user's profile from the Max API and caches it.
    Returns a fallback profile if the API call fails.
    """
    global user_cache
    if sender_id in user_cache:
        return user_cache[sender_id]
    try:
        logging.info("Fetching profile for new user ID: %s", sender_id)
        # Assuming get_contact_details is a lightweight call suitable for this
        response = api.get_contact_details([sender_id])
        response = response['payload']
        api_logger.info(json.dumps(response, indent=4))
        if response and 'contacts' in response and response['contacts']:
            profile = response['contacts'][0]
            user_cache[sender_id] = {'id': profile['id'], 'name': profile['names'][0]['name']}
            return user_cache[sender_id]
    except Exception as e:
        logging.error("Could not fetch profile for ID %s: %s", sender_id, e, exc_info=True)
    return {'id': sender_id, 'name': f'User {sender_id}'}

def forward_max_message_to_group(message: dict, prev_sender: int, sender_profile: dict, forwarded: bool = False):
    """
    Formats and forwards a message from Max to the Telegram group.
    Handles text, replies, and various attachments with improved error handling.
    """
    try:
        max_message_id = message.get('id')
        sender_name = sender_profile.get('name')
        sender_id = sender_profile.get('id')
        text_content = message.get('text', '')
        attachments = message.get('attaches', [])

        if forwarded and text_content:
            text_content = f"Переслано:\n{text_content}"

        if prev_sender is None or prev_sender != sender_id and not forwarded:
            bot.send_message(TG_CHAT_ID, f"{BOT_MESSAGE_PREFIX} *{sender_name}*:", parse_mode="Markdown")

        reply_to_message_id = None
        link_type = message.get('link', {}).get('type')
        if link_type == 'REPLY':
            replied_max_id = message.get('link', {}).get('message', {}).get('id')
            reply_to_message_id = msgs_map.get(replied_max_id)
            if reply_to_message_id:
                logging.debug("Found TG message %s to reply to for Max message %s", reply_to_message_id, max_message_id)
        elif link_type == 'FORWARD':
            forward_msg = message.get('link', {}).get('message')
            if forward_msg:
                # Recursively call to handle the forwarded message content
                forward_max_message_to_group(forward_msg, sender_id, sender_profile, True)

        tg_message_to_map = None
        caption_sent = False
        if attachments:
            logging.debug("Processing message %s with %d attachments.", max_message_id, len(attachments))
            media_group_items = []
            for attach in attachments:
                attach_type = attach.get('_type')
                try:
                    if attach_type == "PHOTO":
                        url = attach.get('baseUrl')
                        file_content = requests.get(url, timeout=REQUESTS_TIMEOUT).content
                        media_group_items.append(types.InputMediaPhoto(file_content))
                    elif attach_type == "VIDEO":
                        video = api.get_video(attach.get('videoId'))
                        media_group_items.append(types.InputMediaVideo(video))
                    elif attach_type == "FILE":
                        file, name = api.get_file(attach.get('fileId'), MAX_CHAT_ID, max_message_id)
                        bot.send_document(TG_CHAT_ID, file, reply_to_message_id, visible_file_name=name, caption="Переслано" if forwarded else None)
                except requests.exceptions.RequestException as e:
                    logging.error("Network error downloading attachment for msg %s: %s", max_message_id, e)
                except Exception as e:
                    logging.error("Failed to process attachment type '%s' for msg %s: %s", attach_type, max_message_id, e, exc_info=True)

            if media_group_items:
                if text_content and not caption_sent:
                    media_group_items[0].caption = text_content
                    caption_sent = True
                sent_messages = bot.send_media_group(TG_CHAT_ID, media_group_items, reply_to_message_id=reply_to_message_id)
                tg_message_to_map = sent_messages[0]

        if not caption_sent and text_content and text_content.strip():
            tg_message_to_map = bot.send_message(TG_CHAT_ID, text_content, reply_to_message_id=reply_to_message_id)

        if tg_message_to_map:
            msgs_map[max_message_id] = tg_message_to_map.message_id
            logging.debug("Mapped Max message %s to TG message %s.", max_message_id, tg_message_to_map.message_id)
    except Exception as e:
        logging.error("Error in forward_max_message_to_group for Max msg %s: %s", message.get('id', 'unknown'), e, exc_info=True)

def edit_tg_message(message):
    max_message_id = message.get('id')
    text = message.get('text')
    tg_message_id = msgs_map[max_message_id]
    bot.edit_message_text(text, TG_CHAT_ID, tg_message_id)

def delete_tg_message(message):
    max_message_id = message.get('id')
    tg_message_id = msgs_map[max_message_id]
    bot.send_message(TG_CHAT_ID, 'Сообщение было удалено', reply_parameters=telebot.types.ReplyParameters(tg_message_id))

def on_max_event(event_data: dict):
    """
    Callback for Max WebSocket events. Filters and forwards new messages.
    """
    global last_message_sender
    api_logger.info(json.dumps(event_data, indent=4))
    if event_data.get("opcode") != 128:
        return
    payload = event_data.get("payload", {})
    message = payload.get("message", {})
    if payload.get("chatId") != MAX_CHAT_ID or message.get('text', '').startswith(BOT_MESSAGE_PREFIX):
        return
    bot.send_chat_action(TG_CHAT_ID, "typing")
    sender_id = message.get('sender')
    sender_profile = get_sender_profile(sender_id)
    logging.info("Received Max message %s from '%s'. Spawning thread to forward.", message.get('id'), sender_profile.get('name'))
    match message.get("status"):
        case "REMOVED":
            delete_tg_message(message)
        case "EDITED":
            edit_tg_message(message)
        case _:
            threading.Thread(target=forward_max_message_to_group, args=(message, last_message_sender, sender_profile)).start()
    last_message_sender = sender_id

# --- Core Logic: Telegram -> Max ---

@bot.message_handler(commands=['send'])
def send_handler(msg: types.Message):
    """Handles /send command in Telegram to forward a message to Max."""
    global last_message_sender
    
    try:
        # Check time
        now = datetime.now().time()
        if msg.from_user.id != ADMIN_USER_ID and not (START_TIME <= now <= END_TIME):
            bot.reply_to(msg, f"Можно отправлять сообщения только между {START_TIME:%H:%M} и {END_TIME:%H:%M}")
            last_message_sender = None
            return
        
        # Check empty message
        text_to_send = msg.text[5:].strip()
        if not text_to_send:
            bot.reply_to(msg, "Нельзя отправить пустое сообщение.")
            last_message_sender = None
            return
        
        # Get username
        first_name = msg.from_user.first_name
        first_name = first_name.replace('\uFE0E', '').replace('\u1160', '')
        if not first_name or first_name.isspace():
            first_name = msg.from_user.username
        username = f"{first_name} {msg.from_user.last_name or ''}".strip()

        # Create full text
        full_text = f"{BOT_MESSAGE_PREFIX} {username} написал(-а):\n{text_to_send}{f'\n{BOT_MESSAGE_PREFIX} {BOT_POST_MESSAGE}' if BOT_POST_MESSAGE else ''}"

        # Get id of replied message in MAX if reply
        reply_to_max_id = None
        if msg.reply_to_message:
            tg_reply_id = msg.reply_to_message.message_id
            reply_to_max_id = next((max_id for max_id, tg_id in msgs_map.items() if tg_id == tg_reply_id), None)

        # Send message
        max_msg = api.send_message(chat_id=MAX_CHAT_ID, text=full_text, reply_id=reply_to_max_id, wait_for_response=True, format=True)

        # If success, map message and send 'Отправлено!'
        msg_id = max_msg['payload'].get('message', {}).get('id')
        if msg_id:
            msgs_map[msg_id] = msg.message_id
            bot.reply_to(msg, 'Отправлено!')
        
        last_message_sender = None
    except Exception as e:
        logging.error("Error in send_handler: %s", e, exc_info=True)
        bot.reply_to(msg, 'Произошла ошибка при отправке.')
        last_message_sender = None

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'document', 'sticker'])
def messages_handle(msg: types.Message):
    """Catches any other message to reset the last_message_sender."""
    global last_message_sender
    last_message_sender = None
    logging.debug("Reset last_message_sender due to activity in TG chat.")

# --- Application Lifecycle, Health, and Restart ---

def restart_program():
    logging.warning("RESTARTING a new instance of the bot...")
    try:
        data_handler.save('msgs', msgs_map) # Save data before restarting
        os.execv(sys.executable, ['python'] + sys.argv)
    except Exception as e:
        logging.critical(f"FATAL: Failed to restart bot: {e}")
        sys.exit(1)

def check_max_api_health():
    try:
        api.send_generic_command('HEARTBEAT', {})
        return True
    except Exception as e:
        logging.warning("Max API health check failed: %s", e)
        return False

def watchdog(polling_thread):
    global max_api_failures
    logging.info("Watchdog thread started. Monitoring bot health.")
    while not shutdown_event.is_set():
        # 1. Check Max API Health
        if not check_max_api_health():
            max_api_failures += 1
            logging.warning(f"Max API connection unstable. Failure count: {max_api_failures}/{MAX_API_FAILURE_THRESHOLD}")
        else:
            if max_api_failures > 0:
                logging.info("Max API connection has recovered.")
            max_api_failures = 0

        # 2. Check if Telegram Polling thread has died
        if not polling_thread.is_alive():
            logging.critical("Telegram polling thread has died unexpectedly.")
            shutdown_event.set()
            restart_program()
            return

        # 3. Trigger restart if failure threshold is met
        if max_api_failures >= MAX_API_FAILURE_THRESHOLD:
            logging.critical(f"Max API has been unresponsive for {max_api_failures} consecutive checks. Triggering restart.")
            shutdown_event.set()
            restart_program()
            return
            
        # Wait for the next check interval
        shutdown_event.wait(HEALTH_CHECK_INTERVAL)

def polling():
    """Target function for the Telegram polling thread with robust error handling."""
    logging.info("Starting Telegram polling...")
    while not shutdown_event.is_set():
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except (requests.exceptions.RequestException, ConnectionResetError) as e:
            logging.warning("Telegram polling network error: %s. Reconnecting in 15 seconds...", e)
            time.sleep(15)
        except Exception as e:
            logging.error("An unexpected error occurred in the polling thread: %s", e, exc_info=True)
            bot.stop_polling()
            time.sleep(30) # Wait longer for unexpected errors before retrying

def run_bot():
    """Initializes and runs the main bot components."""
    global api
    try:
        api = MaxBridge.MaxAPI(auth_token=MAX_AUTH_TOKEN, on_event=on_max_event)
        api.subscribe_to_chat(MAX_CHAT_ID)
    except Exception as e:
        logging.critical("Failed to initialize and connect to Max API: %s", e, exc_info=True)
        sys.exit(1)

    # Send startup message if not sent before
    invite_link = bot.export_chat_invite_link(TG_CHAT_ID)
    start_message = BOT_START_MESSAGE.replace("TG_CHAT_INVITE_LINK", invite_link)
    if start_message and not data_handler.load("started"):
        data_handler.save("started", True)
        api.send_message(MAX_CHAT_ID, start_message, format=True)

    # Start Telegram polling and Watchdog in separate threads
    polling_thread = threading.Thread(target=polling, name="TelebotPolling", daemon=True)
    watchdog_thread = threading.Thread(target=watchdog, args=(polling_thread,), name="Watchdog", daemon=True)
    
    polling_thread.start()
    watchdog_thread.start()
    
    logging.info("Bot started successfully. Transfer is active.")
    return [polling_thread, watchdog_thread]

def main():
    """Main entry point of the application."""
    threads = run_bot()
    
    def shutdown(signum=None, frame=None):
        print("\nShutting down gracefully...")
        logging.info("Shutdown sequence initiated.")
        shutdown_event.set() # Signal all threads to stop
        bot.stop_polling()
        data_handler.save('msgs', msgs_map)
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=5) # Wait for threads to finish
        logging.info("Message ID map saved. Shutdown complete.")
        print("Shutdown complete.")
        sys.exit(0)

    # Handle termination signals for graceful shutdown
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep the main thread alive to handle signals and user input
    if os.getenv("IS_DOCKER"):
        while not shutdown_event.is_set():
            time.sleep(1)
    else:
        print(f'{Fore.YELLOW}Enter "exit" or press Ctrl+C to shutdown.{Style.RESET_ALL}')
        try:
            while True:
                cmd = input().strip().lower()
                if cmd == "exit":
                    shutdown()
                    break
        except (EOFError, KeyboardInterrupt):
             shutdown()

if __name__ == '__main__':

    main()


