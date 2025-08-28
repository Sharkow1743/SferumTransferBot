import logger
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

# Time in seconds to wait between messages
RATE_LIMIT = 1
last_message_time = 0

def send_with_rate_limit(func, *args, **kwargs):
    """
    Wrapper function to rate-limit and handle Telegram API's 429 error.
    Proactively spaces out messages and reactively waits if a rate limit is hit.
    """
    global last_message_time
    
    # 1. Proactive Rate Limiting
    while time.time() - last_message_time < RATE_LIMIT:
        time.sleep(0.1)  # Sleep for a short duration to space out requests

    # 2. Reactive Rate Limiting (with retries)
    while True:
        try:
            last_message_time = time.time()
            return func(*args, **kwargs) # Attempt to send the message
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 429:
                # Extract the 'retry_after' value from the error response
                retry_after = e.result_json.get('parameters', {}).get('retry_after', 5)
                logger.warning(
                    f"Telegram API rate limit hit (429). "
                    f"Waiting for {retry_after} seconds before retrying."
                )
                time.sleep(retry_after) # Wait for the specified duration
                # The 'while True' loop will cause the request to be resent
            else:
                # Re-raise any other Telegram API exceptions
                logger.error(f"An unexpected Telegram API error occurred: {e}")
                raise

if not os.path.isdir('data'):
    os.mkdir('data')
logger.setup_logger()

colorama.init()
colorama.just_fix_windows_console()

# Настройка
startTime = t(7,0) # Время после которого можно отправлять сообщения в сферум
endTime = t(21,0) # Время после которого нельзя отправлять сообщения в сферум
botChr = "⫻" # Что бот добавляет к сообщениям чтобы определить сообщение от бота, которые не надо пересылать
helloMsg = '' # Что бот отправит при первом запуске

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

        messages.sort(key=lambda item: item['conversation_message_id'])

        for message in messages:
            msgId = str(message['conversation_message_id'])
            if not msgId in msgs and not message['text'].startswith(botChr):
                send_with_rate_limit(bot.send_chat_action, chatId, "typing")
                senderProfile = None
                for profile in response['profiles']:
                    userId = profile['id']
                    if not str(userId) in profiles:
                        profiles[userId] = profile
                    if message['from_id'] == userId:
                        senderProfile = profile
                forward_message_to_group(message, last_message_sender, senderProfile)
                last_message_sender = message['from_id']
                data_handler.save('profiles', profiles)
                logger.info(f"Forwarded message {msgId} from {senderProfile['first_name']} {senderProfile['last_name']}")

        time.sleep(5)

def forward_message_to_group(message, last_message_sender, sender_profile, is_forwarded = False):
    try:
        # Извлекаем часто используемые значения
        conversation_message_id = str(message['conversation_message_id'])
        sender_first_name = sender_profile['first_name']
        sender_last_name = sender_profile['last_name']
        sender_sex = sender_profile['sex']
        sender_id = message['from_id']
        text_content = message.get('text', '')
        attachments = message.get('attachments', [])
        action = message.get('action', {})
        action_type = action.get('type', None)

        if is_forwarded:
            text_content = 'Переслано:\n' + text_content

        # Формируем имя отправителя
        sender_name = f"{sender_first_name} {sender_last_name}"
        gender_suffix = "ла" if sender_sex == 1 else "л"

        telegram_message = None

        # Проверяем, нужно ли показывать имя отправителя
        if ((last_message_sender == None or
            last_message_sender != sender_id) and

            (text_content.strip() != '' or
            len(attachments) > 0)):
            send_with_rate_limit(bot.send_message, chatId,
                             f"{botChr} *{sender_name} написа{gender_suffix}:*",
                             parse_mode='MarkdownV2',
                             disable_notification=True
            )

        if action and action_type:
            text = ''
            match action_type:
                case 'chat_invite_user':
                    id = action.get('member_id')
                    name = profiles[id]['first_name_acc']
                    if sender_id != id:
                        text = f'{sender_name} пригласи{gender_suffix} {name}'
                    else:
                        text
                case 'chat_invite_user_by_link':
                    text = f'{sender_name} заш{gender_suffix if sender_sex == 1 else "ё" + gender_suffix} по ссылке-приглашению'
                case 'chat_kick_user':
                    id = action.get('member_id')
                    name = profiles[id]['first_name_acc']
                    text = f'{sender_name} исключи{gender_suffix} {name}'

            telegram_message = send_with_rate_limit(bot.send_message, chatId, text, disable_notification=True)


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

                send_with_rate_limit(bot.send_document,
                    chatId,
                    doc_content,
                    visible_file_name=doc_title,
                    caption='Переслано' if is_forwarded else '')
                logger.info(f"Sent document: {doc_title}")

            elif attachment_type == "video":
                text_content = "[Видео]\n" + text_content

        # Обрабатываем ответ на сообщение
        reply_to_message_id = None
        if 'reply_message' in message:
            reply_conversation_id = str(message['reply_message']['conversation_message_id'])
            if reply_conversation_id in msgs:
                reply_to_message_id = msgs[reply_conversation_id]

        if 'fwd_messages' in message and len(message['fwd_messages']) > 0:
            for fwd_message in message['fwd_messages']:
                fwd_message['conversation_message_id'] = conversation_message_id
                forward_message_to_group(fwd_message, last_message_sender, sender_profile, True)

        # Отправляем сообщение
        if len(media_items) > 0:
            telegram_message = send_with_rate_limit(bot.send_media_group,
                chatId,
                media_items,
                reply_to_message_id=reply_to_message_id)
            logger.info(f"Sent media group with {len(media_items)} items")
        elif text_content and text_content.strip():
            telegram_message = send_with_rate_limit(bot.send_message,
                chatId,
                text_content,
                reply_to_message_id=reply_to_message_id)

            # Логируем укороченный текст сообщения
            log_text = text_content[:50] + ("..." if len(text_content) > 50 else "")
            logger.info(f"Sent text message: {log_text.replace('\n', '\\n')}")

        # Сохраняем соответствие ID сообщений
        tg_msg_id = telegram_message.id if telegram_message else None
        if not is_forwarded:
            if not conversation_message_id in msgs:
                msgs[conversation_message_id] = tg_msg_id
        elif msgs.get(conversation_message_id, None):
            msgs[conversation_message_id] = tg_msg_id
        data_handler.save('msgs', msgs)
        print('saved')
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

            firstName = users.get(msg.from_user.id, None)
            if not firstName:
                send_with_rate_limit(bot.reply_to, msg, 'Укажите своё имя с помощью комманды "/set_name <имя>"')
                return
            username = f"{firstName}"

            text = f"{botChr} *{username} написал(-а):*\n{msg.text[5:]}"

            replyId = None
            if msg.reply_to_message:
                replyId = list(msgs.keys())[list(msgs.values()).index(msg.reply_to_message.id)]

            try:
                response = api.send_message(peer_id=vkChatId, text=text, format=True, reply_to_id=replyId)
                if 'cmid' in response:
                    logger.info(f"Message {msg.id} sent to Sferum")
                    send_with_rate_limit(bot.reply_to, msg, 'Отправлено!')
                    msgs[str(response['cmid'])] = msg.id
                    last_message_sender = None
                    data_handler.save('msgs', msgs)
                elif 'error_code' in response:
                    raise RuntimeError(response['error_msg'])

            except Exception as e:
                logger.error(f"Error sending message to Sferum - {type(e).__name__}: {str(e)}")
                logger.debug("Full error details:", exc_info=True)
        else:
            send_with_rate_limit(bot.reply_to, msg, f"Можно отправлять сообщения только между {startTime} и {endTime}")
            logger.warning(f"Message received outside allowed time window: {now}")
    except Exception as e:
        logger.error(f"Error in send_handler - {type(e).__name__}: {str(e)}")
        logger.debug("Full error details:", exc_info=True)

@bot.message_handler(['set_name'])
def set_name(msg):
    name = msg.text[10:]
    users[msg.from_user.id] = name
    data_handler.save('users', users)

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
        logger.info("Fetcher stopped")
        run_fetcher()
    except KeyboardInterrupt:
        logger.info("Fetcher interrupted by user")
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
    data_handler.save('msgs', {})
    if helloMsg:
        api.send_message(vkChatId, helloMsg, format=True)

if __name__ == '__main__':
    if not data_handler.load('started'):
        start()

    msgs = data_handler.load('msgs') or {} #VK id:TG id
    users = data_handler.load('users') or {} #TG id:name
    profiles = data_handler.load('profiles') or {} #VK id:name

    fetcher_thread = threading.Thread(target=run_fetcher, name="FetcherThread")
    fetcher_thread.start()
    polling_thread = threading.Thread(target=run_polling, name="PollingThread")
    polling_thread.start()

    logger.info("Bot started successfully")
    print(f'{Fore.YELLOW}Enter "exit" or ^c to shutdown.{Style.RESET_ALL}')

    def shutdown(a = None, b = None):
        logger.info("Shutdown...")
        bot.stop_polling()
        data_handler.save('msgs', msgs)
        data_handler.save('users', users)
        data_handler.save('profiles', profiles)
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
                        send_with_rate_limit(bot.send_message, chatId, "Бот написал:")
                        send_with_rate_limit(bot.send_message, chatId, msg)
                    case "sendvk":
                        msg = str(input())
                        msg = f"{botChr} Бот написал:\n{msg}"
                        api.send_message(vkChatId, msg)
        except KeyboardInterrupt:
            logger.debug("Shutdown by keyboard interrupt")
            shutdown()