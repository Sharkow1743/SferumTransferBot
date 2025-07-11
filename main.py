import SferumAPI
import telebot
from datetime import datetime, time as t
import time
import requests
import os
from dotenv import load_dotenv
import threading
import colorama
from colorama import Fore, Back, Style
import logging
from logging.handlers import RotatingFileHandler
import json

colorama.init()
colorama.just_fix_windows_console()

# Настройка
startTime = t(7,0) # Время после которого можно отправлять сообщения в сферум
endTime = t(22,0) # Время после которого нельзя отправлять сообщения в сферум
botMsg = "Я - бот." # Что бот добавляет к сообщению когда отправляет в сферум

load_dotenv()

vkChatId = os.getenv('VK_CHAT_ID')
chatId = os.getenv('TG_CHAT_ID')
token = os.getenv('TG_TOKEN')
remixdsid = os.getenv('VK_COOKIE')
sentMessages = {}

api = SferumAPI.SferumAPI(remixdsid=remixdsid)
bot = telebot.TeleBot(token)

# Инициализация логгера
def setup_logger():
    log_file = 'bot.log'
    api_log_file = 'api_responses.log'  # New file for API responses
    
    # Clear log files if they exist
    for file_path in [log_file, api_log_file]:
        try:
            with open(file_path, 'w'):
                pass  # This clears the file contents
        except IOError as e:
            print(f"Warning: Could not clear log file {file_path} - {e}")

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Set to lowest level for handlers to filter
    
    # Main logger formatter
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # API response logger formatter (simpler format)
    api_formatter = logging.Formatter('%(asctime)s - %(message)s')
    
    # Main log file handler (as before)
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=1*1024*1024,
        backupCount=1,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    
    # Console handler (as before)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)
    
    # New handler for API responses (separate file)
    api_handler = RotatingFileHandler(
        api_log_file,
        maxBytes=1*1024*1024,
        backupCount=1,
        encoding='utf-8'
    )
    api_handler.setFormatter(api_formatter)
    api_handler.setLevel(logging.INFO)  # We'll use INFO level for API responses
    api_handler.addFilter(lambda record: record.name == 'api_logger')  # Only log API responses
    
    # Create a separate logger for API responses
    api_logger = logging.getLogger('api_logger')
    api_logger.setLevel(logging.INFO)
    api_logger.addHandler(api_handler)
    api_logger.propagate = False  # Prevent propagation to root logger
    
    # Suppress verbose logs from libraries (as before)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("telebot").setLevel(logging.WARNING)
    
    return logger, api_logger  # Return both loggers

# Get both loggers when initializing
logger, api_logger = setup_logger()

# Then modify your fetch_and_forward_messages function to log API responses:
def fetch_and_forward_messages():
    last_message_sender = None
    last_message_id = load_last_message_id()
    logger.info("Starting message fetcher")
    
    while True:
        try:
            response = api.messages.get_history(peer_id=vkChatId, count=10, offset=0)
            
            # Log the API response to the separate file
            try:
                api_logger.info(f"API Response:\n{json.dumps(response, indent=2, ensure_ascii=False)}")
            except Exception as e:
                api_logger.error(f"Could not log API response: {str(e)}")
            
            response = response['response']
            
        except Exception as e:
            logger.error(f"Error fetching messages - {type(e).__name__}: {str(e)}")
            logger.debug("Full error details:", exc_info=True)
            time.sleep(5)
            continue
        
        messages = sorted(response['items'], key=lambda msg: msg['id'])
        
        for message in messages:
            if last_message_id is None or message['id'] > last_message_id and not message['text'].startswith("#"):
                bot.send_chat_action(chatId, "typing")
                last_message_id = message['id']
                save_last_message_id(last_message_id)
                senderProfile = None
                for profile in response['profiles']:
                    if message['from_id'] == profile['id']:
                        senderProfile = profile
                forward_message_to_group(message, last_message_sender, senderProfile)
                last_message_sender = message['from_id']
                logger.info(f"Forwarded message {message['id']} from {senderProfile['first_name']} {senderProfile['last_name']}")
            
            if message['text'].startswith("#"):
                try:
                    lines = message['text'].splitlines()
                    if len(lines) > 0:
                        last_line = lines[-1]
                        if botMsg in last_line:
                            msgId = last_line[len(botMsg)+4:].strip()
                            if msgId in sentMessages:
                                msg = sentMessages[msgId]
                                sentMessages.pop(msgId)
                                bot.reply_to(msg, "Отправлено")
                                logger.info(f"Confirmed delivery of message {msgId}")
                except Exception as e:
                    logger.error(f"Error processing bot message - {type(e).__name__}: {str(e)}")
                    logger.debug("Full error details:", exc_info=True)
                    continue
                    
        time.sleep(5)

def forward_message_to_group(message, last_message_sender, senderProfile):
    try:
        senderName = f"{senderProfile['first_name']} {senderProfile['last_name']}"
        text = message['text']
        if last_message_sender is None or last_message_sender != message['from_id']:
            bot.send_message(chatId, senderName + " написа" + ("ла" if senderProfile['sex'] == 1 else "л") + ":")
        media = []
        for i, attachment in enumerate(message["attachments"]):
            type = attachment["type"]
            if type == "photo":
                imgUrl = attachment["photo"]["sizes"][-1]["url"]
                img = requests.get(imgUrl).content
                if i == 0:
                    media.append(telebot.types.InputMediaPhoto(img, text))
                else:
                    media.append(telebot.types.InputMediaPhoto(img))
            elif type == "doc":
                docUrl = attachment["doc"]["url"]
                doc = requests.get(docUrl).content
                bot.send_document(chatId, doc, visible_file_name=attachment["doc"]["title"])
                logger.info(f"Sent document: {attachment['doc']['title']}")
            elif type == "video":
                text = "[Видео]\n" + text

        if len(media) > 0:
            bot.send_media_group(chatId, media)
            logger.info(f"Sent media group with {len(media)} items")
        elif text and text != "":
            bot.send_message(chatId, text)
            logger.info(f"Sent text message: {text[:50]}{"..." if len(text) > 50 else ""}")
    except Exception as e:
        logger.error(f"Error forwarding message {message.get('id', 'unknown')} - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)

def load_last_message_id():
    try:
        with open("last_message_id.txt", "r") as f:
            last_message_id = f.read().strip() 
            return int(last_message_id) if last_message_id else None
    except FileNotFoundError:
        logger.warning("last_message_id.txt not found, starting from scratch")
        return None
    except Exception as e:
        logger.error(f"Error loading last message ID - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)
        return None

def save_last_message_id(message_id):
    try:
        with open("last_message_id.txt", "w") as f:
            f.write(str(message_id))
        logger.debug(f"Saved last message ID: {message_id}")
    except Exception as e:
        logger.error(f"Error saving last message ID - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)

@bot.message_handler(['send'])
def send_handler(msg):
    try:
        now = datetime.now().time()
        if now > startTime and now < endTime:
            sentMessages[str(msg.id)] = msg
            logger.info(f"Received message to send from {msg.from_user.username} (ID: {msg.id})")

            firstName = msg.from_user.first_name
            lastName = msg.from_user.last_name if msg.from_user.last_name != None else ""
            username = f"{firstName} {lastName}"
            
            sysPart = f"# {botMsg} @{msg.id}"

            text = f"# {username} написал(-а):\n{msg.text[5:]}\n{sysPart}"
            
            try:
                api.messages.send_message(peer_id=vkChatId, text=text)
                logger.info(f"Message {msg.id} sent to Sferum")
            except Exception as e:
                logger.error(f"Error sending message to Sferum - {type(e).__name__}: {str(e)}")
                logger.debug("Full error details:", exc_info=True)
        else:
            bot.reply_to(msg, f"Можно отправлять сообщения только между {startTime} и {endTime}")
            logger.warning(f"Message received outside allowed time window: {now}")
    except Exception as e:
        logger.error(f"Error in send_handler - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)

def run_fetcher():
    try:
        logger.info("Starting fetcher thread")
        fetch_and_forward_messages()
    except Exception as e:
        logger.critical(f"Fatal error in fetcher - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)
    except KeyboardInterrupt:
        logger.info("Fetcher interrupted by user")
    finally:
        logger.info("Fetcher stopped")

fetcher_thread = threading.Thread(target=run_fetcher, name="FetcherThread")
fetcher_thread.start()

def run_polling():
    try:
        logger.info("Starting polling thread")
        bot.infinity_polling()
    except Exception as e:
        logger.critical(f"Fatal error in polling - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)
    except KeyboardInterrupt:
        logger.info("Polling interrupted by user")
    finally:
        logger.info("Polling stopped")

polling_thread = threading.Thread(target=run_polling, name="PollingThread")
polling_thread.start()

logger.info("Bot started successfully")
print(f'{Fore.YELLOW}Enter "exit" or ^c to shutdown.{Style.RESET_ALL}')

try:
    while True:
        if str(input()) == "exit":
            logger.info("Shutdown command received")
            bot.stop_polling()
            os._exit(0)
except KeyboardInterrupt:
    logger.info("Shutdown by keyboard interrupt")
    bot.stop_polling()
    os._exit(0)
