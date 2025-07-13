import logger
logger.setup_logger()
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
import json
import data_handler
import SferumBridge

colorama.init()
colorama.just_fix_windows_console()

# Настройка
startTime = t(0,0) # Время после которого можно отправлять сообщения в сферум
endTime = t(22,0) # Время после которого нельзя отправлять сообщения в сферум
botMsg = "Я - бот." # Что бот добавляет к сообщению когда отправляет в сферум
botChr = "⫻"

load_dotenv()

vkChatId = os.getenv('VK_CHAT_ID')
chatId = os.getenv('TG_CHAT_ID')
token = os.getenv('TG_TOKEN')
remixdsid = os.getenv('VK_COOKIE')
botMsgs = []
last_message_sender = None
msgs = data_handler.load('msgs') or {} #VK id:TG id

api = SferumBridge.SferumAPI(remixdsid=remixdsid)
bot = telebot.TeleBot(token)

# Get both loggers when initializing
logger = logging.getLogger()
api_logger = logging.getLogger("api_logger")

# Then modify your fetch_and_forward_messages function to log API responses:
def fetch_and_forward_messages():
    last_message_id = data_handler.load('msgId')
    logger.info("Starting message fetcher")
    global api
    global last_message_sender
    while True:
        try:
            response = api.get_history(peer_id=vkChatId, count=10)
            
            # Log the API response to the separate file
            try:
                api_logger.info(f"API Response:\n{json.dumps(response, indent=2, ensure_ascii=False)}")
            except Exception as e:
                api_logger.error(f"Could not log API response: {str(e)}")
            
        except Exception as e:
            logger.error(f"Error fetching messages - {type(e).__name__}: {str(e)}")
            logger.debug("Full error details:", exc_info=True)
            time.sleep(5)
            continue
        
        messages = response['items']
        
        for message in messages:
            if last_message_id is None or message['id'] > last_message_id and not message['text'].startswith(botChr):
                bot.send_chat_action(chatId, "typing")
                last_message_id = message['id']
                data_handler.save('msgId', last_message_id)
                senderProfile = None
                for profile in response['profiles']:
                    if message['from_id'] == profile['id']:
                        senderProfile = profile
                forward_message_to_group(message, last_message_sender, senderProfile)
                last_message_sender = message['from_id']
                logger.info(f"Forwarded message {message['id']} from {senderProfile['first_name']} {senderProfile['last_name']}")
                    
        time.sleep(5)

def forward_message_to_group(message, last_message_sender, senderProfile):
    try:
        senderName = f"{senderProfile['first_name']} {senderProfile['last_name']}"
        text = message['text']
        if last_message_sender is None or last_message_sender != message['from_id']:
            bot.send_message(chatId, senderName + " написа" + ("ла" if senderProfile['sex'] == 1 else "л") + ":")
        media = []
        for i, attachment in enumerate(message["attachments"]):
            attachment_type = attachment["type"]
            if attachment_type == "photo":
                imgUrl = attachment["photo"]["sizes"][-1]["url"]
                img = requests.get(imgUrl).content
                if i == 0:
                    media.append(telebot.types.InputMediaPhoto(img, text))
                else:
                    media.append(telebot.types.InputMediaPhoto(img))
            elif attachment_type == "doc":
                docUrl = attachment["doc"]["url"]
                doc = requests.get(docUrl).content
                bot.send_document(chatId, doc, visible_file_name=attachment["doc"]["title"])
                logger.info(f"Sent document: {attachment['doc']['title']}")
            elif attachment_type == "video":
                text = "[Видео]\n" + text

        reply = None
        if 'reply_message' in message and message['reply_message']['id'] in msgs:
            reply = msgs[message['reply_message']['id']]

        tgMsg = None
        if len(media) > 0:
            tgMsg = bot.send_media_group(chatId, media, reply_to_message_id=reply)
            logger.info(f"Sent media group with {len(media)} items")
        elif text and text != "":
            tgMsg = bot.send_message(chatId, text, reply_to_message_id=reply)
            logger.info(f"Sent text message: {text[:50]}{"..." if len(text) > 50 else ""}".replace("\n","\\n"))

        if tgMsg.id and not tgMsg.id in msgs:
            msgs[message['conversation_message_id']] = tgMsg.id
    except Exception as e:
        logger.error(f"Error forwarding message {message.get('id', 'unknown')} - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)

@bot.message_handler(['send'])
def send_handler(msg):
    try:
        now = datetime.now().time()
        if now > startTime and now < endTime:
            logger.info(f"Received message to send from {msg.from_user.username} (ID: {msg.id})")

            firstName = msg.from_user.first_name
            lastName = msg.from_user.last_name if msg.from_user.last_name != None else ""
            username = f"{firstName} {lastName}"
            
            sysPart = f"{botChr} /{botMsg}/"

            text = f"*{botChr} {username} написал(-а):*\n{msg.text[5:]}\n{sysPart}"

            try:
                response = api.send_message(peer_id=vkChatId, text=text, format=True)
                if 'cmid' in response:
                    logger.info(f"Message {msg.id} sent to Sferum")
                    bot.reply_to(msg, 'Отправлено!')
                elif 'error_code' in response:
                    raise RuntimeError(response['error_msg'])
                
            except Exception as e:
                logger.error(f"Error sending message to Sferum - {type(e).__name__}: {str(e)}")
                logger.debug("Full error details:", exc_info=True)
        else:
            bot.reply_to(msg, f"Можно отправлять сообщения только между {startTime} и {endTime}")
            logger.warning(f"Message received outside allowed time window: {now}")
    except Exception as e:
        logger.error(f"Error in send_handler - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)

@bot.message_handler(func=lambda m: True)
def messages_handle(msg):
    global last_message_sender
    last_message_sender = None

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

if __name__ == '__main__':
    fetcher_thread = threading.Thread(target=run_fetcher, name="FetcherThread")
    fetcher_thread.start()
    polling_thread = threading.Thread(target=run_polling, name="PollingThread")
    polling_thread.start()

    logger.info("Bot started successfully")
    print(f'{Fore.YELLOW}Enter "exit" or ^c to shutdown.{Style.RESET_ALL}')



    def shutdown():
        bot.stop_polling()
        data_handler.save('msgs', msgs)
        os._exit(0)

    try:
        while True:
            if str(input()) == "exit":
                logger.info("Shutdown command received")
                shutdown()

    except KeyboardInterrupt:
        logger.info("Shutdown by keyboard interrupt")
        shutdown()
