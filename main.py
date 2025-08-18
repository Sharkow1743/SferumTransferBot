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
from colorama import Fore, Style
import logging
import json
import data_handler
import SferumBridge
import signal

colorama.init()
colorama.just_fix_windows_console()

# Настройка
startTime = t(7,0) # Время после которого можно отправлять сообщения в сферум
endTime = t(21,0) # Время после которого нельзя отправлять сообщения в сферум
botMsg = "Я - бот." # Что бот добавляет к сообщению когда отправляет в сферум
botChr = "⫻" # Что бот добавляет к сообщениям чтобы определить сообщение от бота, которые не надо пересылать
helloMsg = f'{botChr} Привет! Я - бот. [Мой гитхаб](https://github.com/Sharkow1743/sferumTransferBot)\n{botChr} Я пересылаю все сообщения из этого чата в [телеграм](https://telegram.org/)' # Что бот отправит при первом запуске

load_dotenv()

vkChatId = os.getenv('VK_CHAT_ID')
chatId = os.getenv('TG_CHAT_ID')
token = os.getenv('TG_TOKEN')
remixdsid = os.getenv('VK_COOKIE')
admin_user_id = os.getenv('ADMIN_USER_ID')
last_message_sender = None

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
    
    count = 200
    while True:
        try:
            response = api.get_history(peer_id=vkChatId, count=count)
            count = 20
            
            # Log the API response to the separate file
            try:
                api_logger.info(f"API Response:\n{json.dumps(response, indent=2, ensure_ascii=False)}")
            except Exception as e:
                api_logger.error(f"Could not log API response: {str(e)}")

            if 'items' in response:
                messages = response['items']
            elif 'error_msg' in response:
                raise RuntimeError(response['error_msg'])
        except Exception as e:
            logger.error(f"Error fetching messages - {type(e).__name__}: {str(e)}")
            logger.debug("Full error details:", exc_info=True)
            time.sleep(5)
            continue
        
        for message in messages:
            if last_message_id is None or message['conversation_message_id'] > last_message_id and not message['text'].startswith(botChr):
                bot.send_chat_action(chatId, "typing")
                last_message_id = message['conversation_message_id']
                data_handler.save('msgId', last_message_id)
                senderProfile = None
                for profile in response['profiles']:
                    if message['from_id'] == profile['id']:
                        senderProfile = profile
                forward_message_to_group(message, last_message_sender, senderProfile)
                last_message_sender = message['from_id']
                logger.info(f"Forwarded message {message['conversation_message_id']} from {senderProfile['first_name']} {senderProfile['last_name']}")
                    
        time.sleep(5)

def forward_message_to_group(message, last_message_sender, sender_profile):
    try:
        # Извлекаем часто используемые значения
        conversation_message_id = str(message['conversation_message_id'])
        sender_first_name = sender_profile['first_name']
        sender_last_name = sender_profile['last_name']
        sender_sex = sender_profile['sex']
        sender_id = message['from_id']
        text_content = message.get('text', '')
        attachments = message.get('attachments', [])
        
        # Формируем имя отправителя
        sender_name = f"{sender_first_name} {sender_last_name}"
        gender_suffix = "ла" if sender_sex == 1 else "л"
        
        # Проверяем, нужно ли показывать имя отправителя
        if last_message_sender == None or last_message_sender != sender_id:
            bot.send_message(chatId, f"{sender_name} написа{gender_suffix}:")
        
        # Обрабатываем вложения
        media_items = []
        for i, attachment in enumerate(attachments):
            attachment_type = attachment["type"]
            
            if attachment_type == "photo":
                # Получаем фото с максимальным разрешением
                photo_sizes = attachment["photo"]["sizes"]
                largest_photo_url = photo_sizes[-1]["url"]
                photo_content = requests.get(largest_photo_url).content
                
                # Первое фото содержит текст сообщения
                if i == 0:
                    media_items.append(
                        telebot.types.InputMediaPhoto(photo_content, text_content))
                else:
                    media_items.append(
                        telebot.types.InputMediaPhoto(photo_content))
            
            elif attachment_type == "doc":
                # Отправляем документ отдельным сообщением
                doc_url = attachment["doc"]["url"]
                doc_title = attachment["doc"]["title"]
                doc_content = requests.get(doc_url).content
                
                bot.send_document(
                    chatId, 
                    doc_content, 
                    visible_file_name=doc_title)
                logger.info(f"Sent document: {doc_title}")
            
            elif attachment_type == "video":
                text_content = "[Видео]\n" + text_content
        
        # Обрабатываем ответ на сообщение
        reply_to_message_id = None
        if 'reply_message' in message:
            reply_conversation_id = int(message['reply_message']['conversation_message_id'])
            if reply_conversation_id in msgs:
                reply_to_message_id = msgs[reply_conversation_id]
        
        if 'fwd_messages' in message and len(message['fwd_messages']) > 0:
            for fwd_message in message['fwd_messages']:
                fwd_message['text'] = "Пересланно:\n" + fwd_message['text']
                forward_message_to_group(fwd_message, last_message_sender, sender_profile)
        
        # Отправляем сообщение
        telegram_message = None
        if len(media_items) > 0:
            telegram_message = bot.send_media_group(
                chatId, 
                media_items, 
                reply_to_message_id=reply_to_message_id)
            logger.info(f"Sent media group with {len(media_items)} items")
        
        elif text_content and text_content.strip():
            telegram_message = bot.send_message(
                chatId, 
                text_content, 
                reply_to_message_id=reply_to_message_id)
            
            # Логируем укороченный текст сообщения
            log_text = text_content[:50] + ("..." if len(text_content) > 50 else "")
            logger.info(f"Sent text message: {log_text.replace('\n', '\\n')}")
        
        # Сохраняем соответствие ID сообщений
        if telegram_message and hasattr(telegram_message, 'id'):
            if conversation_message_id not in msgs:
                msgs[int(conversation_message_id)] = telegram_message.id
    except Exception as e:
        error_message = (f"Error forwarding message {message.get('conversation_message_id', 'unknown')} - "
                         f"{type(e).__name__}: {str(e)}")
        logger.error(error_message)
        logger.debug("Full error details:", exc_info=True)

@bot.message_handler(['send'])
def send_handler(msg):
    global last_message_sender
    try:
        now = datetime.now().time()
        if str(msg.from_user.id) == admin_user_id or (now > startTime and now < endTime):
            logger.info(f"Received message to send from {msg.from_user.username} (ID: {msg.id})")

            firstName = msg.from_user.first_name
            lastName = msg.from_user.last_name if msg.from_user.last_name != None else ""
            username = f"{firstName} {lastName}"
            
            sysPart = f"{botChr} /{botMsg}/"

            text = f"{botChr} *{username} написал(-а):*\n{msg.text[5:]}\n{sysPart}"

            replyId = None
            if msg.reply_to_message:
                replyId = list(msgs.keys())[list(msgs.values()).index(msg.reply_to_message.id)]

            try:
                response = api.send_message(peer_id=vkChatId, text=text, format=True, reply_to_id=replyId)
                if 'cmid' in response:
                    logger.info(f"Message {msg.id} sent to Sferum")
                    bot.reply_to(msg, 'Отправлено!')
                    msgs[int(response['cmid'])] = msg.id
                    last_message_sender = None
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

def start():
    data_handler.save('started', True)
    data_handler.save('msgId', 0)
    data_handler.save('msgs', {})
    api.send_message(vkChatId, helloMsg, format=True)

if __name__ == '__main__':
    if not data_handler.load('started'):
        start()

    msgs = data_handler.load('msgs') or {} #VK id:TG id

    fetcher_thread = threading.Thread(target=run_fetcher, name="FetcherThread")
    fetcher_thread.start()
    polling_thread = threading.Thread(target=run_polling, name="PollingThread")
    polling_thread.start()

    logger.info("Bot started successfully")
    print(f'{Fore.YELLOW}Enter "exit" or ^c to shutdown.{Style.RESET_ALL}')

    def shutdown():
        logger.info("Shutdown...")
        bot.stop_polling()
        data_handler.save('msgs', msgs)
        os._exit(0)

    if os.getenv("IS_DOCKER"):
        signal.signal(signal.SIGTERM, shutdown)
        while True:
            time.sleep(1)
    else:
        try:
            while True:
                match str(input()).lower():
                    case "exit":
                        shutdown()
                    case "stop":
                        shutdown()
                    case "sendtg":
                        msg = str(input())
                        bot.send_message(chatId, "Бот написал:")
                        bot.send_message(chatId, msg)
                    case "sendvk":
                        msg = str(input())
                        msg = f"{botChr} Бот написал:\n{msg}"
                        api.send_message(vkChatId, msg)
        except KeyboardInterrupt:
            logger.debug("Shutdown by keyboard interrupt")
            shutdown()
