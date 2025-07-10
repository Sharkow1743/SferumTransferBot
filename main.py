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

colorama.init()
colorama.just_fix_windows_console()

# Настройка
startTime = t(7,0) # Время после которого можно отправлять сообщения в сферум
endTime = t(22,0) # Время после которого нельзя отправлять сообщения в сферум
botMsg = "Я - бот."

# Инициализация

load_dotenv()

vkChatId = os.getenv('VK_CHAT_ID')
chatId = os.getenv('TG_CHAT_ID')
token = os.getenv('TG_TOKEN')
remixdsid = os.getenv('VK_COOKIE')
sentMessages = {}

api = SferumAPI.SferumAPI(remixdsid=remixdsid)
bot = telebot.TeleBot(token)

def fetch_and_forward_messages():
    last_message_sender = None
    last_message_id = load_last_message_id()
    while True:
        try:
            response = api.messages.get_history(peer_id=vkChatId, count=10, offset=0)
            response = response['response']
        except Exception as e:
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
            msgId = message['text'].splitlines()[-1][len(botMsg)+4:].strip()
            if message['text'].startswith("#") and msgId in sentMessages:
                msg = sentMessages[msgId]
                sentMessages.pop(msgId)
                bot.reply_to(msg, "Отправлено")
        time.sleep(5)

def forward_message_to_group(message, last_message_sender, senderProfile):
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

        
            
    if len(media) > 0:
        bot.send_media_group(chatId, media)
    elif text and text != "":
        bot.send_message(chatId, text)

def load_last_message_id():
    with open("last_message_id.txt", "r") as f:
        last_message_id = f.read().strip() 
        return int(last_message_id)

def save_last_message_id(message_id):
    f = open("last_message_id.txt", "w")
    f.write(str(message_id))
    f.close()

def extract_arg(arg):
    return arg.split()[1:]

@bot.message_handler(['send'])
def send_handler(msg):
    now = datetime.now().time()
    if now > startTime and now < endTime:
        sentMessages[str(msg.id)] = msg

        firstName = msg.from_user.first_name
        lastName = msg.from_user.last_name if msg.from_user.last_name != None else ""
        username = f"{firstName} {lastName}"
        
        sysPart = f"# {botMsg} @{msg.id}"

        text = f"# {username} написал(-а):\n{msg.text[5:]}\n{sysPart}"
        
        try:
            api.messages.send_message(peer_id=vkChatId, text=text)
        except TypeError:
            pass
    else:
        bot.reply_to(msg, f"Можно отправлять сообщения только между {startTime} и {endTime}")


def run_fetcher():
    try:
        fetch_and_forward_messages()
    except Exception as e:
        print(f"Error in fetcher: {e}")
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        print("Fetcher stopped.")

fetcher_thread = threading.Thread(target=run_fetcher)
fetcher_thread.start()

def run_polling():
    try:
        bot.infinity_polling()
    except Exception as e:
        print(f"Error in polling: {e}")
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        print("Polling stopped.")


polling_thread = threading.Thread(target=run_polling)
polling_thread.start()

print(Fore.YELLOW)
print("The bot is running.")
print("Enter 'exit' to shutdown.")
print(Style.RESET_ALL)

while True:
    if str(input()) == "exit":
        bot.stop_polling()
        os._exit(0)
