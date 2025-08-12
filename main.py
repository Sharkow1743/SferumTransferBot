# Standard Library Imports
import os
import sys
import threading
import logging
from datetime import datetime, time as t
import json
import time

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

logger.setup_logger()
api_logger = logging.getLogger("api_logger")

# --- Initial Setup ---
colorama.init(autoreset=True)
load_dotenv()

# --- Constants & Configuration ---
# Time window during which messages can be sent from Telegram to Max
START_TIME = t(7, 0)
END_TIME = t(22, 0)

BOT_MESSAGE_SIGNATURE = "Я - бот"
BOT_MESSAGE_PREFIX = "⫻"
BOT_START_MESSAGE = f"{BOT_MESSAGE_PREFIX} Привет! {BOT_MESSAGE_SIGNATURE}. Я пересылаю все сообщения отсюда в [телеграм](TG_CHAT_INVITE_LINK).\n{BOT_MESSAGE_PREFIX} [Мой гитхаб](https://github.com/Sharkow1743/sferumTransferBot)"

# --- Environment Variables ---
try:
    MAX_CHAT_ID = int(os.getenv('VK_CHAT_ID'))
    TG_CHAT_ID = os.getenv('TG_CHAT_ID')
    TG_TOKEN = os.getenv('TG_TOKEN')
    MAX_AUTH_TOKEN = os.getenv('VK_COOKIE')
    ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID'))
    if not all([MAX_CHAT_ID, TG_CHAT_ID, TG_TOKEN, MAX_AUTH_TOKEN]):
        raise ValueError("One or more environment variables are not set.")
except (ValueError, TypeError) as e:
    print(f"{Fore.RED}FATAL: Configuration error - {e}. Please check your .env file.{Style.RESET_ALL}")
    sys.exit(1)


# --- State and Globals ---
# Maps Max message ID (str) to the corresponding Telegram message ID (int) for replies.
# Loaded from and saved to a file by data_handler.
msgs_map = data_handler.load('msgs') or {}

# In-memory cache for user profiles to reduce API calls. {max_user_id: profile_dict}
user_cache = {}

# Stores the ID of the last user who sent a message to avoid repeating names.
last_message_sender = None

# --- API Initialization ---
bot = telebot.TeleBot(TG_TOKEN, threaded=False) # threaded=False is often safer with manual threading
api = None # Will be initialized in the main block

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
        response = api.get_contact_details([sender_id])
        response = response['payload']
        api_logger.info(json.dumps(response, indent=4))
        
        if response and 'contacts' in response and response['contacts']:
            profile = response['contacts'][0]
            # Cache a simplified version of the profile
            user_cache[sender_id] = {
                'id': profile['id'],
                'name': profile['names'][0]['name'],
            }
            return user_cache[sender_id]
    except Exception as e:
        logging.error("Could not fetch profile for ID %s: %s", sender_id, e, exc_info=True)

    # Return a fallback profile if the API call fails or yields no data
    return {'id': sender_id, 'name': f'User {sender_id}'}

def forward_max_message_to_group(message: dict, prev_sender: int, sender_profile: dict):
    """
    Formats and forwards a message from Max to the Telegram group.
    Handles text, replies, and various attachments (photos, videos, docs, stickers).
    """
    try:
        max_message_id = message.get('id')
        sender_name = sender_profile.get('name')
        sender_id = sender_profile.get('id')
        text_content = message.get('text', '')
        attachments = message.get('attaches', [])

        # To avoid clutter, only show the sender's name if they are new or different
        if prev_sender is None or prev_sender != sender_id:
            bot.send_message(TG_CHAT_ID, f"*{sender_name}*:", parse_mode="Markdown")

        # Determine if this message is a reply and find the TG message ID to reply to
        reply_to_message_id = None
        if message.get('link', {}).get('type') == 'REPLY':
            replied_max_id = message.get('link', {}).get('message', {}).get('id')
            reply_to_message_id = msgs_map.get(replied_max_id)
            if reply_to_message_id:
                 logging.info("Found corresponding TG message %s to reply to for Max message %s", reply_to_message_id, max_message_id)

        # --- ATTACHMENT PROCESSING LOGIC ---
        tg_message_to_map = None
        caption_sent = False     # Flag to ensure message text is sent only once (as a caption)

        if attachments:
            logging.info("Processing message %s with %d attachments.", max_message_id, len(attachments))
            media_group_items = []
            
            # 1. Categorize attachments to handle them correctly
            for attach in attachments:
                attach_type = attach.get('_type')
                
                match attach_type:
                    case "PHOTO":
                        try:
                            url = attach.get('baseUrl')
                            file_content = requests.get(url).content
                            media_group_items.append(types.InputMediaPhoto(file_content))
                        except Exception as e:
                            logging.error("Failed to process photo attachment for msg %s: %s", max_message_id, e, exc_info=True)

                    case "VIDEO":
                        try:
                            video = api.get_video(attach.get('videoId'))
                            
                            media_group_items.append(types.InputMediaVideo(video))
                        except Exception as e:
                            logging.error("Failed to process video attachment for msg %s: %s", max_message_id, e, exc_info=True)
                    case "FILE":
                        try:
                            file, name = api.get_file(attach.get('fileId'), MAX_CHAT_ID, max_message_id)

                            bot.send_document(TG_CHAT_ID, file, reply_to_message_id, visible_file_name=name)
                        except Exception as e:
                            logging.error("Failed to process file attachment for msg %s: %s", max_message_id, e, exc_info=True)
                
                # TODO: Add handler for "STICKER"

            # 2. Send grouped media (photos/videos)
            if media_group_items:
                # Attach the text content as a caption to the first item in the group
                if text_content and not caption_sent:
                    media_group_items[0].caption = text_content
                    caption_sent = True

                sent_messages = bot.send_media_group(
                    TG_CHAT_ID, media_group_items,
                    reply_to_message_id=reply_to_message_id
                )
                # Map the ID of the first message in the album for replies
                tg_message_to_map = sent_messages[0]
                logging.info("Sent %d items as a media group for Max msg %s.", len(media_group_items), max_message_id)

        # 3. If there's text content that wasn't sent as a caption, send it now
        if not caption_sent and text_content and text_content.strip():
            tg_message_to_map = bot.send_message(
                TG_CHAT_ID,
                text_content,
                reply_to_message_id=reply_to_message_id
            )

        # 4. Save the mapping from Max message ID to the new Telegram message ID
        if tg_message_to_map:
            msgs_map[max_message_id] = tg_message_to_map.message_id
            logging.info("Successfully mapped Max message %s to TG message %s.", max_message_id, tg_message_to_map.message_id)

    except Exception as e:
        logging.error("Error in forward_max_message_to_group for Max msg %s: %s", message.get('id', 'unknown'), e, exc_info=True)


def on_max_event(event_data: dict):
    """
    Callback function executed on every event from the Max WebSocket.
    Filters for new messages in the target chat and forwards them.
    """
    global last_message_sender
    api_logger.info(json.dumps(event_data, indent=4))

    # We only care about new messages (opcode 128) that haven't been removed
    if event_data.get("opcode") != 128 or event_data.get("status") == "REMOVED":
        return

    payload = event_data.get("payload", {})
    message = payload.get("message", {})
    
    # Ignore messages not from our target chat or messages sent by the bot itself
    if payload.get("chatId") != MAX_CHAT_ID or message.get('text', '').startswith(BOT_MESSAGE_PREFIX):
        return
    
    bot.send_chat_action(TG_CHAT_ID, "typing")
    
    sender_id = message.get('sender')
    sender_profile = get_sender_profile(sender_id)
    
    logging.info(
        "Received Max message %s from '%s'. Spawning thread to forward.",
        message.get('id'), sender_profile.get('name')
    )

    # Use a thread to forward the message to avoid blocking the WebSocket listener
    forwarding_thread = threading.Thread(
        target=forward_max_message_to_group,
        args=(message, last_message_sender, sender_profile)
    )
    forwarding_thread.start()
    
    # Update the last sender to enable message chaining without name repetition
    last_message_sender = sender_id

# --- Core Logic: Telegram -> Max ---

@bot.message_handler(commands=['send'])
def send_handler(msg: types.Message):
    """
    Handles /send command in Telegram to forward a message to Max.
    Constructs the message text and handles replies.
    """
    global last_message_sender
    try:
        # Enforce the time window for sending messages
        now = datetime.now().time()
        if msg.from_user.id != ADMIN_USER_ID and not (START_TIME <= now <= END_TIME):
            bot.reply_to(msg, f"Можно отправлять сообщения только между {START_TIME:%H:%M} и {END_TIME:%H:%M}")
            logging.warning(
                "Message from '%s' blocked due to time restrictions. Current time: %s",
                msg.from_user.username, now
            )
            return
            
        logging.info("Received /send command from '%s' (TG Msg ID: %s)", msg.from_user.username, msg.message_id)
        
        # Extract text and user info for the message
        text_to_send = msg.text[5:].strip()
        if not text_to_send:
            bot.reply_to(msg, "Нельзя отправить пустое сообщение.")
            return

        first_name = msg.from_user.first_name
        last_name = msg.from_user.last_name or ""
        username = f"{first_name} {last_name}".strip()
        
        # Format the message to clearly indicate it came from the Telegram bridge
        sys_part = f"{BOT_MESSAGE_PREFIX} {BOT_MESSAGE_SIGNATURE}"
        full_text = f"{BOT_MESSAGE_PREFIX} *{username} написал(-а):*\n{text_to_send}\n{sys_part}"
        
        # Find the Max message ID to reply to, if any
        reply_to_max_id = None
        if msg.reply_to_message:
            tg_reply_id = msg.reply_to_message.message_id
            # Invert the msgs_map to find the Max ID from the TG ID
            for max_id, tg_id in msgs_map.items():
                if tg_id == tg_reply_id:
                    reply_to_max_id = max_id
                    break
        
        max_msg = api.send_message(chat_id=MAX_CHAT_ID, text=full_text, reply_id=reply_to_max_id, wait_for_response=True, format=True)
        id = max_msg['payload'].get('message', {}).get('id')
        if id:
            msgs_map[id] = msg.message_id
            logging.info("Successfully mapped Max message %s to TG message %s.", id, msg.message_id)
        
            bot.reply_to(msg, 'Отправлено!')
            logging.info("Sent message from '%s' to Max. Replied to Max ID: %s", username, reply_to_max_id)
        
            # Reset last sender so the next Max message will show the sender's name
            last_message_sender = None

    except Exception as e:
        logging.error("Error in send_handler: %s", e, exc_info=True)
        bot.reply_to(msg, 'Произошла ошибка при отправке.')
        last_message_sender = None

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'document', 'sticker'])
def messages_handle(msg: types.Message):
    """
    Catches any other message to reset the last_message_sender.
    This ensures that after any interaction in the TG group, the next
    message from Max will correctly display the sender's name.
    """
    global last_message_sender
    last_message_sender = None
    logging.debug("Reset last_message_sender due to activity in TG chat.")

# --- Main Application ---
if __name__ == '__main__':
    def shutdown():
        """Gracefully shuts down the bot, saving state."""
        print("\nShutting down...")
        logging.info("Shutdown sequence initiated.")
        bot.stop_polling()
        data_handler.save('msgs', msgs_map)
        logging.info("Message ID map saved. Shutdown complete.")
        print("Shutdown complete.")
        sys.exit(0)

    def pooling():
        logging.info("Starting Telegram polling...")
        while True:
            try:
                # We add a timeout to the polling call itself as a first line of defense
                bot.infinity_polling(timeout=60, long_polling_timeout=30)
            except requests.exceptions.RequestException as e:
                # This is a broad catch for all network-related errors from the requests library
                logging.warning("Telegram polling failed with a network error: %s", e)
                logging.info("Restarting polling in 15 seconds...")
                time.sleep(15)
            except Exception as e:
                # Catch any other unexpected errors to prevent the thread from crashing
                logging.warning("An unexpected error occurred in the polling thread: %s", e, exc_info=True)
                bot.stop_polling()
                logging.info("Restarting polling in 30 seconds...")
                time.sleep(30)

    try:
        # Initialize the Max API and subscribe to chat events
        api = MaxBridge.MaxAPI(auth_token=MAX_AUTH_TOKEN, on_event=on_max_event)
        api.subscribe_to_chat(MAX_CHAT_ID)
    except Exception as e:
        logging.critical("Failed to initialize and connect to Max API: %s", e, exc_info=True)
        sys.exit(1)

    BOT_START_MESSAGE = BOT_START_MESSAGE.replace("TG_CHAT_INVITE_LINK", bot.export_chat_invite_link(TG_CHAT_ID))
    if BOT_START_MESSAGE != "" and not data_handler.load("started"):
        data_handler.save("started", True)
        api.send_message(MAX_CHAT_ID, BOT_START_MESSAGE, format=True)

    # Start the Telegram polling in a separate, daemonized thread
    polling_thread = threading.Thread(target=pooling, name="TelebotPolling", daemon=True)
    polling_thread.start()

    logging.info("Bot started successfully. Transfer is active.")
    print(f'{Fore.YELLOW}Enter "exit" or press Ctrl+C to shutdown.{Style.RESET_ALL}')

    try:
        # Keep the main thread alive to listen for a shutdown command
        while True:
            if input().strip().lower() == "exit":
                logging.info("'exit' command received. Shutting down.")
                break
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt received. Shutting down.")
    finally:
        shutdown()