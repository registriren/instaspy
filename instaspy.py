import sqlite3
import codecs
import os
import shutil
import datetime
import time
import subprocess
from botapitamtam import BotHandler
import json
import logging
from threading import Thread

try:
    import urllib.request as urllib
except ImportError:
    import urllib as urllib

try:
    from instagram_private_api import (
        Client, ClientError, ClientLoginError,
        ClientCookieExpiredError, ClientLoginRequiredError,
        __version__ as client_version)
except ImportError:
    import sys

    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    from instagram_private_api import (
        Client, ClientError, ClientLoginError,
        ClientCookieExpiredError, ClientLoginRequiredError,
        __version__ as client_version)

from instagram_private_api import ClientError
from instagram_private_api import Client

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

config = 'config.json'
with open(config, 'r', encoding='utf-8') as c:
    conf = json.load(c)
    token = conf['access_token']
    username = conf['username']
    password = conf['password']

bot = BotHandler(token)

if not os.path.isfile('users.db'):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE users
                      (chat_id INTEGER PRIMARY KEY , subscribe TEXT, history TEXT,
                       delay TEXT)
                   """)
    conn.commit()
    c.close()
    conn.close()

conn = sqlite3.connect("users.db", check_same_thread=False)


def to_json(python_object):
    if isinstance(python_object, bytes):
        return {'__class__': 'bytes',
                '__value__': codecs.encode(python_object, 'base64').decode()}
    raise TypeError(repr(python_object) + ' is not JSON serializable')


def from_json(json_object):
    if '__class__' in json_object and json_object.get('__class__') == 'bytes':
        return codecs.decode(json_object.get('__value__').encode(), 'base64')
    return json_object


def onlogin_callback(api, settings_file):
    cache_settings = api.settings
    with open(settings_file, 'w') as outfile:
        json.dump(cache_settings, outfile, default=to_json)
        logger.info('New auth cookie file was made: {0!s}'.format(settings_file))


def login(username="", password=""):
    device_id = None
    try:
        settings_file = "credentials.json"
        if not os.path.isfile(settings_file):
            # settings file does not exist
            logger.warning('Unable to find auth cookie file: {0!s} (creating a new one...)'.format(settings_file))

            # login new
            api = Client(
                username, password,
                on_login=lambda x: onlogin_callback(x, settings_file))
        else:
            with open(settings_file) as file_data:
                cached_settings = json.load(file_data, object_hook=from_json)

            device_id = cached_settings.get('device_id')
            # reuse auth settings
            api = Client(
                username, password,
                settings=cached_settings)

            logger.info('Using cached login cookie for "' + api.authenticated_user_name + '".')

    except (ClientCookieExpiredError, ClientLoginRequiredError) as e:
        logger.error('ClientCookieExpiredError/ClientLoginRequiredError: {0!s}'.format(e))

        # Login expired
        # Do relogin but use default ua, keys and such
        if username and password:
            api = Client(
                username, password,
                device_id=device_id,
                on_login=lambda x: onlogin_callback(x, settings_file))
        else:
            logger.error("The login cookie has expired, but no login arguments were given.\n"
                         "Please supply --username and --password arguments.")
            return

    except ClientLoginError as e:
        logger.error('Could not login: {:s}.\n[E] {:s}\n\n{:s}'.format(
            json.loads(e.error_response).get("error_title", "Error title not available."),
            json.loads(e.error_response).get("message", "Not available"), e.error_response))
        return
    except ClientError as e:
        logger.error('Client Error: {:s}'.format(e.error_response))
        return
    except Exception as e:
        if str(e).startswith("unsupported pickle protocol"):
            logger.warning("This cookie file is not compatible with Python {}.".format(sys.version.split(' ')[0][0]))
            logger.warning("Please delete your cookie file 'credentials.json' and try again.")
        else:
            logger.error('Unexpected Exception: {0!s}'.format(e))
        return

    logger.info('Login to "' + api.authenticated_user_name + '" OK!')
    cookie_expiry = api.cookie_jar.auth_expires
    logger.info('Login cookie expiry date: {0!s}'.format(
        datetime.datetime.fromtimestamp(cookie_expiry).strftime('%Y-%m-%d at %I:%M:%S %p')))

    return api


# Downloader


def check_directories(user_to_check):
    try:
        if not os.path.isdir(os.getcwd() + "/stories/{}/".format(user_to_check)):
            os.makedirs(os.getcwd() + "/stories/{}/".format(user_to_check))
        return True
    except Exception:
        return False


def get_media_story(user_to_check, user_id, ig_client, chat_id, no_video_thumbs=False):
    try:
        try:
            feed = ig_client.user_story_feed(user_id)
        except Exception as e:
            logger.warning("An error occurred trying to get user feed: " + str(e))
            return False
        try:
            feed_json = feed['reel']['items']
            open("feed_json.json", 'w').write(json.dumps(feed_json))
        except TypeError as e:
            logger.info("There are no recent stories to process for this user:" + str(e))
            return False

        list_video = []
        list_image = []

        list_video_new = []
        list_image_new = []

        for media in feed_json:
            taken_ts = datetime.datetime.utcfromtimestamp(media.get('taken_at', "")).strftime(
                    '%Y-%m-%d_%H-%M-%S')

            is_video = 'video_versions' in media and 'image_versions2' in media

            if 'video_versions' in media:
                list_video.append([media['video_versions'][0]['url'], taken_ts])
            if 'image_versions2' in media:
                if (is_video and not no_video_thumbs) or not is_video:
                    list_image.append([media['image_versions2']['candidates'][0]['url'], taken_ts])

            logger.info("Downloading video stories. ({:d} stories detected)".format(len(list_video)))
            for index, video in enumerate(list_video):
                filename = video[0].split('/')[-1]
                try:
                    final_filename = video[1] + ".mp4"
                except:
                    final_filename = filename.split('.')[0] + ".mp4"
                    logger.error(
                        "Could not determine timestamp filename for this file, using default: " + final_filename)
                save_path = os.getcwd() + "/stories/{}/".format(user_to_check) + final_filename
                if not search_history(chat_id, final_filename):
                    logger.info(
                        "({:d}/{:d}) Downloading video: {:s}".format(index + 1, len(list_video), final_filename))
                    try:
                        download_file(video[0], save_path)
                        list_video_new.append(save_path)
                        add_history(chat_id, final_filename)
                    except Exception as e:
                        logger.warning("An error occurred while iterating video stories: " + str(e))
                        return False
                else:
                    logger.info("Story already exists: {:s}".format(final_filename))

        logger.info("Downloading image stories. ({:d} stories detected)".format(len(list_image)))
        for index, image in enumerate(list_image):
            filename = (image[0].split('/')[-1]).split('?', 1)[0]
            try:
                final_filename = image[1] + ".jpg"
            except:
                final_filename = filename.split('.')[0] + ".jpg"
                logger.error(
                    "Could not determine timestamp filename for this file, using default: " + final_filename)
            save_path = os.getcwd() + "/stories/{}/".format(user_to_check) + final_filename
            if not search_history(chat_id, final_filename):
                logger.info("({:d}/{:d}) Downloading image: {:s}".format(index + 1, len(list_image), final_filename))
                try:
                    download_file(image[0], save_path)
                    list_image_new.append(save_path)
                    add_history(chat_id, final_filename)
                except Exception as e:
                    logger.warning("An error occurred while iterating image stories: " + str(e))
                    return False
            else:
                logger.info("Story already exists: {:s}".format(final_filename))

        if (len(list_image_new) != 0) or (len(list_video_new) != 0):
            logger.info("Story downloading ended with " + str(len(list_image_new)) + " new images and " + str(
                len(list_video_new)) + " new videos downloaded.")
            key_link = bot.button_link('Открыть в Instagram', 'https://instagram.com/{}'.format(user_to_check))
            key = bot.attach_buttons([key_link])
            attach = bot.attach_image(list_image_new) + bot.attach_video(list_video_new) + key
            bot.send_content(attach, chat_id, text='Новые истории от *{}*'.format(user_to_check))
            shutil.rmtree(os.getcwd() + "/stories/{}".format(user_to_check), ignore_errors=False, onerror=None)
        else:
            logger.info("No new stories were downloaded.")
    except Exception as e:
        logger.error("A general error occurred: " + str(e))
        return False
    except KeyboardInterrupt as e:
        logger.info("User aborted download:" + str(e))
        return False


def download_file(url, path, attempt=0):
    try:
        urllib.urlretrieve(url, path)
    except Exception as e:
        # if not attempt == 1:
        #    attempt += 1
        #    logger.error("({:d}) Download failed(file): {:s}.".format(attempt, str(e)))
        #    logger.warning("Trying again in 5 seconds.")
        #    time.sleep(5)
        #    download_file(url, path, attempt)
        # else:
        logger.error("Retry failed three times, skipping file.")


def command_exists(command):
    try:
        fnull = open(os.devnull, 'w')
        subprocess.call([command], stdout=fnull, stderr=subprocess.STDOUT)
        return True
    except OSError:
        return False


def check_user(user):
    ig_client = login(username, password)
    try:
        user_res = ig_client.username_info(user)
        user_id = user_res['user']['pk']
        follow_res = ig_client.friendships_show(user_id)
        if follow_res.get("is_private") and not follow_res.get("following"):
            raise Exception("You are not following this private user.")
        return True
    except:
        return False


def start_download(users_to_check, chat_id, novideothumbs=True):
    ig_client = login(username, password)
    logger.info("Stories will be downloaded to {:s}".format(os.getcwd()))

    def download_user(index, user, attempt=0):
        try:
            if not user.isdigit():
                user_res = ig_client.username_info(user)
                user_id = user_res['user']['pk']
            else:
                user_id = user
                user_info = ig_client.user_info(user_id)
                if not user_info.get("user", None):
                    raise Exception("No user is associated with the given user id.")
                else:
                    user = user_info.get("user").get("username")
            logger.info("Getting stories for: {:s}".format(user))
            if check_directories(user):
                follow_res = ig_client.friendships_show(user_id)
                if follow_res.get("is_private") and not follow_res.get("following"):
                    raise Exception("You are not following this private user.")
                get_media_story(user, user_id, ig_client, chat_id, novideothumbs)
            else:
                logger.error("Could not make required directories. Please create a 'stories' folder manually.")
                return False
            if (index + 1) != len(users_to_check):
                logger.info('({}/{}) 5 second time-out until next user...'.format((index + 1), len(users_to_check)))
                time.sleep(5)
        except Exception as e:
            # if not attempt == 1:
            #    attempt += 1
            #    logger.error("({:d}) Download failed(user): {:s}.".format(attempt, str(e)))
            #    logger.warning("Trying again in 5 seconds.")
            #    time.sleep(5)
            #    download_user(index, user, attempt)
            # else:
            logger.error("Retry failed three times, skipping user.")
            return False
        return True

    for index, user_to_check in enumerate(users_to_check):
        try:
            status = download_user(index, user_to_check)
            # print('status = ', status)
            if not status:
                return False
        except KeyboardInterrupt:
            logger.info("The operation was aborted.")
            return False
    return True


def search_history(chat_id, name):
    c = conn.cursor()
    c.execute("SELECT chat_id FROM users WHERE history LIKE '%{}%' AND chat_id= {}".format(name, chat_id))
    dat = c.fetchone()
    c.close()
    return dat


def get_history(chat_id):
    c = conn.cursor()
    c.execute("SELECT history FROM users WHERE chat_id= {}".format(chat_id))
    dat = c.fetchone()
    if dat != None:
        dat = dat[0]
    else:
        dat = None
    c.close()
    return dat


def add_history(chat_id, history):
    c = conn.cursor()
    res = get_history(chat_id)
    if res:
        if history not in res.split(' '):
            history = res + ' ' + history
        else:
            return
    try:
        c.execute(
            "INSERT INTO users (chat_id, history) VALUES ({}, '{}')".format(chat_id, history))
        logger.info('New history was made for user {0!s}'.format(chat_id))
    except:
        c.execute(
            "UPDATE users SET history = '{}' WHERE chat_id = {}".format(history, chat_id))
        logger.info('Update history for user {0!s}'.format(chat_id))
    conn.commit()
    c.close()


def update_delay(chat_id):
    now = datetime.datetime.now()
    delay = now.strftime("%d-%m-%Y %H:%M")
    c = conn.cursor()
    c.execute("UPDATE users SET delay = '{}' WHERE chat_id = {}".format(delay, chat_id))
    logger.info('Update delay for user {}'.format(chat_id))
    conn.commit()
    c.close()
    return


def get_delay(chat_id):
    c = conn.cursor()
    c.execute("SELECT delay FROM users WHERE chat_id= {}".format(chat_id))
    dat = c.fetchone()
    if dat != None:
        dat = dat[0]
    c.close()
    return dat


def delay(chat_id):
    now = datetime.datetime.now()
    then = get_delay(chat_id)
    if then != None:
        then = datetime.datetime.strptime(then, "%d-%m-%Y %H:%M")
    else:
        then = now
    delta = now - then
    if delta.seconds < 3600:
        return False
    else:
        logger.info('Delta delay more or equal norm for user {}'.format(chat_id))
        update_delay(chat_id)
        return True


def get_list_chats():
    c = conn.cursor()
    c.execute("SELECT chat_id FROM users WHERE chat_id")
    lst = c.fetchall()
    dat = []
    if lst != None:
        dat = [lst[i][0] for i in range(len(lst))]
    c.close()
    return dat


def update_stories():
    while True:
        chats = get_list_chats()
        for chat in chats:
            if delay(chat):
                users = get_subscribe(chat)
                user = users.split(' ')
                start_download(user, chat)
                del_history(chat)
            time.sleep(5)


def chat_status_control():
    #chats = bot.get_all_chats()
    chats = get_list_chats()
    #chats = chats['chats']
    for i in chats:
        print(i)
        print(bot.get_chat(i))


def get_subscribe(chat_id):
    c = conn.cursor()
    c.execute("SELECT subscribe FROM users WHERE chat_id= {}".format(chat_id))
    logger.info('Get subscribe for {}'.format(chat_id))
    dat = c.fetchone()
    if dat != None:
        dat = dat[0]
    else:
        dat = None
    c.close()
    return dat


def add_subscribe(chat_id, subscribe):
    now = datetime.datetime.now()
    now = now - datetime.timedelta(seconds=3550)
    delay = now.strftime("%d-%m-%Y %H:%M")
    res = get_subscribe(chat_id)
    c = conn.cursor()
    if res:
        if subscribe not in res.split(' '):
            subscribe = res + ' ' + subscribe
        else:
            return
    try:
        c.execute(
            "INSERT INTO users (chat_id, subscribe, delay) VALUES ({}, '{}', '{}')".format(chat_id, subscribe, delay))
        logger.info('Creating a new subscribe for {}'.format(chat_id))
    except:
        c.execute(
            "UPDATE users SET subscribe = '{}', delay = '{}' WHERE chat_id = {}".format(subscribe, delay, chat_id))
        logger.info('Update subscribe for {}'.format(chat_id))
    conn.commit()
    c.close()


def subscribe(text, chat_id):
    if len(text) << 100 and text != None:
        res = get_subscribe(chat_id)
        if res:
            res = res.split(' ')
        else:
            res = []
        if len(res) < 10:
            upd = bot.send_message('Получаю информацию пользователя *{}* ...'.format(text), chat_id)
            mid = bot.get_message_id(upd)
            if check_user(text):
                bot.delete_message(mid)
                add_subscribe(chat_id, text)
                bot.send_message('Вы подписаны на истории пользователя: *{}*'.format(text), chat_id)
            else:
                bot.delete_message(mid)
                bot.send_message('Ошибка. Возможно пользователя *{}* не существует или он ограничил доступ к своим данным'.format(text), chat_id)
        else:
            bot.send_message('Невозможно. Число Ваших подписок уже достигло 10', chat_id)


def del_history(chat):
    hist = get_history(chat)
    now = datetime.datetime.now() - datetime.timedelta(days=4)
    now = now.strftime("%Y-%m-%d")
    if hist:
        hist = hist.split(' ')
        hist = [x for x in hist if now not in x]
        hist = ' '.join(hist)
        c = conn.cursor()
        c.execute(
            "UPDATE users SET history = '{}' WHERE chat_id = {}".format(hist, chat))
        conn.commit()
        c.close()
        logger.info('Delete not relevant history for user {0!s}'.format(chat))
    else:
        return


def del_all_subscribe(chat_id):
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE chat_id= {}".format(chat_id))
    logger.info('Delete all subscribe for {}'.format(chat_id))
    conn.commit()
    c.close()


def del_subscribe(user, chat_id):
    users = get_subscribe(chat_id)
    users = users.split(' ')
    users.remove(user)
    users = ' '.join(users)
    c = conn.cursor()
    c.execute("UPDATE users SET subscribe = '{}' WHERE chat_id = {}".format(users, chat_id))
    logger.info('Unsubscribe @{} for user {}'.format(user, chat_id))
    conn.commit()
    c.close()


def menu(callback_id, chat_id, notifi=None):
    key1 = bot.button_callback('\U0001F4CB Список подписок', 'list')
    key2 = bot.button_callback('\U0001F4DD Подписаться', 'subscribe')
    key3 = bot.button_callback('\U0000274C Отписаться', 'unsubscribe')
    key4 = bot.button_callback('\U0001F525 Отписаться от всех', 'allunsubscribe')
    key = [key1, key2, key3, key4]
    if callback_id != None:
        button = bot.attach_buttons(key)
        message = {"text": 'Выберите действие',
                   "attachments": button}
        upd = bot.send_answer_callback(callback_id, notification=notifi, message=message)
    else:
        upd = bot.send_buttons('Выберите действие', key, chat_id)
    mid = bot.get_message_id(upd)
    return mid


def list_subscribe(callback_id, chat_id):
    key = []
    mid = None
    back = bot.button_callback('🏠Назад', 'home', intent='positive')
    users = get_subscribe(chat_id)
    if users:
        for user in users.split(' '):
            button = bot.button_callback('@{}'.format(user), user)
            key.append(button)
        key.append(back)
        if callback_id != None:
            button = bot.attach_buttons(key)
            message = {"text": 'Подписки',
                       "attachments": button}
            upd = bot.send_answer_callback(callback_id, notification=None, message=message)
        else:
            upd = bot.send_buttons('Подписки', key, chat_id)
        mid = bot.get_message_id(upd)
    return mid



def main():
    marker = None
    mid_m = None
    mid_d = None
    cmd = None
    while True:
        update = bot.get_updates(marker, limit=1)
        if update is None:
            #chat_status_control()
            continue
        marker = bot.get_marker(update)
        type_upd = bot.get_update_type(update)
        text = bot.get_text(update)
        chat_id = bot.get_chat_id(update)
        payload = bot.get_payload(update)
        cbid = bot.get_callback_id(update)
        if mid_m != None:
            mid_d = mid_m
        if type_upd == 'bot_started':
            mid_m = menu(callback_id=cbid, chat_id=chat_id)
        if type_upd == 'message_created':
            if text.lower() == 'menu' or text.lower() == '/menu':
                bot.delete_message(mid_d)
                payload = 'home'
            else:
                bot.delete_message(mid_d)
                subscribe(text, chat_id)
        if payload == 'home':
            mid_m = menu(callback_id=cbid, chat_id=chat_id)
        users = get_subscribe(chat_id)
        if not users:
            list_users = []
        else:
            list_users = users.split(' ')
        if payload == 'subscribe':
            bot.delete_message(mid_d)
            bot.send_message('Отправьте имя пользователя для подписки', chat_id)
        elif payload and not users:
            menu(callback_id=cbid, chat_id=chat_id, notifi='У Вас нет подписок')
        elif payload in list_users:
            if cmd == 'unsubscribe':
                notify = 'Вы отписались от @{}'.format(payload)
                mid_m = menu(callback_id=cbid, chat_id=chat_id, notifi=notify)
                del_subscribe(payload, chat_id)
            elif cmd != None:
                mid_m = menu(callback_id=cbid, chat_id=chat_id, notifi='Жду команду')
        elif payload == 'allunsubscribe':
            menu(callback_id=cbid, chat_id=chat_id, notifi='Вы отписаны от всех пользователей')
            del_all_subscribe(chat_id)
            cmd = None
        elif payload == 'list' or payload == 'unsubscribe':
            mid_m = list_subscribe(callback_id=cbid, chat_id=chat_id)
            cmd = payload


update_thred = Thread(target=update_stories)
update_thred.start()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        exit()
