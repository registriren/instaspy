import sqlite3
import os
import shutil
import datetime
import time
import instaloader
from botapitamtam import BotHandler
import json
import logging
from threading import Thread
try:
    import urllib.request as urllib
except ImportError:
    import urllib as urllib

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.ERROR)
logger = logging.getLogger(__name__)

config = 'config.json'
with open(config, 'r', encoding='utf-8') as c:
    conf = json.load(c)
    token = conf['access_token']
    username = conf['username']
    password = conf['password']
    admin = conf['admin_userid']

bot = BotHandler(token)

L = instaloader.Instaloader()
try:
    L.load_session_from_file(username)
    logger.info('Load session from file is OK')
except Exception as e:
    logger.info('Load session false. Create new coockies file: ', str(e))
    try:
        L.login(username, password)
        L.save_session_to_file()
    except instaloader.exceptions.ConnectionException as e:
        logger.error('Login Failed! - ', str(e))

if not os.path.isfile('users.db'):
    conn = sqlite3.connect("users.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE users
                      (chat_id INTEGER PRIMARY KEY , subscribe TEXT, history TEXT,
                       delay TEXT)
                   """)
    c.execute("""CREATE TABLE consumer
                          (id INTEGER PRIMARY KEY, user_id TEXT)
                       """)
    conn.commit()
    c.close()
    conn.close()

conn = sqlite3.connect("users.db", check_same_thread=False)


def check_directories(user_to_check):
    try:
        if not os.path.isdir(os.getcwd() + "/stories/{}/".format(user_to_check)):
            os.makedirs(os.getcwd() + "/stories/{}/".format(user_to_check))
        return True
    except Exception:
        return False


def download_file(url, path):
    try:
        urllib.urlretrieve(url, path)
        urllib.urlcleanup()
    except Exception as e:
        logger.error("Retry failed three times, skipping file. - {}".format(e))


def check_user(user):
    try:
        profile = L.check_profile_id(user)
        if profile.userid:
            return True
    except Exception as e:
        logger.warning("No check_user: %s.", e)
        time.sleep(30)
        return False


def get_media_story(user_to_check, chat_id):
    try:
        try:
            profile = L.check_profile_id(user_to_check)
        except Exception as e:
            logger.error('Instagram profile not found: {}'.format(e))
            return False
        list_video_new = []
        list_image_new = []
        for story in L.get_stories(userids=[profile.userid]):
            for item in story.get_items():
                date_history = item.date_utc.strftime("%d-%m-%Y_%H:%M:%S")
                if item.typename == 'GraphStoryVideo':
                    if not search_history(chat_id, date_history):
                        logger.info("Downloading video: {}.mp4 for chat_id {}".format(item.date_utc, chat_id))
                        try:
                            save_path = os.getcwd() + '/stories/{}/{}.mp4'.format(user_to_check, item.date_utc)
                            download_file(item.video_url, save_path)
                            list_video_new.append(save_path)
                            add_history(chat_id, date_history)
                        except Exception as e:
                            logger.warning("An error occurred while iterating video stories: " + str(e))
                            return False
                elif item.typename == 'GraphStoryImage':
                    if not search_history(chat_id, date_history):
                        logger.info("Downloading image: {}.jpg for chat_id {}".format(item.date_utc, chat_id))
                        try:
                            save_path = os.getcwd() + '/stories/{}/{}.jpg'.format(user_to_check, str(item.date_utc))
                            download_file(item.url, save_path)
                            list_image_new.append(save_path)
                            add_history(chat_id, date_history)
                        except Exception as e:
                            logger.warning("An error occurred while iterating image stories: " + str(e))
                            return False

        if (len(list_image_new) != 0) or (len(list_video_new) != 0):
            try:
                list_video_new.reverse()
                list_image_new.reverse()
            except Exception:
                logger.info('REVERSE: One of the lists is empty')
            logger.info("Story downloading ended with " + str(len(list_image_new)) + " new images and " + str(
                len(list_video_new)) + " new videos downloaded.")
            key_link = bot.button_link('Открыть в Instagram', 'https://instagram.com/{}'.format(user_to_check))
            key = bot.attach_buttons([key_link])
            attach = bot.attach_image(list_image_new) + bot.attach_video(list_video_new) + key
            bot.send_message('Новые истории от {}'.format(user_to_check), chat_id, attachments=attach)
            shutil.rmtree(os.getcwd() + "/stories/{}".format(user_to_check), ignore_errors=False, onerror=None)
        else:
            logger.info("No new stories were downloaded.")
    except Exception as e:
        logger.error("A general error occurred: " + str(e))
        return False
    except KeyboardInterrupt as e:
        logger.info("User aborted download:" + str(e))
        return False


def start_download(users_to_check, chat_id):
    logger.info("Stories will be downloaded to {:s}".format(os.getcwd()))

    def download_user(index, user):
        try:
            logger.info("Getting stories for: {:s}".format(user))
            if check_directories(user):
                get_media_story(user, chat_id)
            else:
                logger.error("Could not make required directories. Please create a 'stories' folder manually.")
                return False
            if (index + 1) != len(users_to_check):
                logger.info('({}/{}) 23 second time-out until next user...'.format((index + 1), len(users_to_check)))
                time.sleep(100)
        except Exception:
            logger.error("Retry failed three times, skipping user.")
            return False
        return True

    for index, user_to_check in enumerate(users_to_check):
        try:
            status = download_user(index, user_to_check)
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
    if dat:
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
    if dat:
        dat = dat[0]
    c.close()
    return dat


def delay(chat_id):
    now = datetime.datetime.now()
    then = get_delay(chat_id)
    if then:
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
    if lst:
        dat = [lst[i][0] for i in range(len(lst))]
    c.close()
    return dat


def chat_status_control():
    # chats = bot.get_all_chats()
    chats = get_list_chats()
    # chats = chats['chats']
    for i in chats:
        print(i)
        print(bot.get_chat(i))


def get_subscribe(chat_id):
    c = conn.cursor()
    c.execute("SELECT subscribe FROM users WHERE chat_id= {}".format(chat_id))
    logger.info('Get subscribe for {}'.format(chat_id))
    dat = c.fetchone()
    if dat:
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
    if len(text) << 100 and text:
        res = get_subscribe(chat_id)
        if res:
            res = res.split(' ')
        else:
            res = []
        if len(res) < 10:
            upd = bot.send_message('Получаю информацию пользователя {} ...'.format(text), chat_id)
            mid = bot.get_message_id(upd)
            if check_user(text):
                bot.delete_message(mid)
                add_subscribe(chat_id, text)
                bot.send_message('Вы подписаны на истории пользователя: {}'.format(text), chat_id)
            else:
                bot.delete_message(mid)
                bot.send_message(
                    'Ошибка. Возможно пользователя {} не существует или он ограничил доступ к своим данным'.format(
                        text), chat_id)
        else:
            bot.send_message('Невозможно. Число Ваших подписок уже достигло 10', chat_id)


def del_history(chat):
    hist = get_history(chat)
    now = datetime.datetime.now() - datetime.timedelta(days=4)
    now = now.strftime("%d-%m-%Y")
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
    key5 = bot.button_callback('Добавить получателя', 'addconsumer')
    key6 = bot.button_callback('Удалить получателя', 'delconsumer')
    key = [[key1], [key2], [key3], [key4], [key5], [key6]]
    if callback_id:
        button = bot.attach_buttons(key)
        upd = bot.send_answer_callback(callback_id, notification=notifi, attachments=button)
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
            button = bot.button_callback('{}'.format(user), user)
            key.append([button])
        key.append([back])
        if callback_id:
            button = bot.attach_buttons(key)
            upd = bot.send_answer_callback(callback_id, notification=None, attachments=button)
        else:
            upd = bot.send_buttons('Подписки', key, chat_id)
        mid = bot.get_message_id(upd)
    return mid


def list_consumers(callback_id, chat_id):
    key = []
    mid = None
    back = bot.button_callback('🏠Назад', 'home', intent='positive')
    users = get_consumers()
    if users:
        for user in users:
            button = bot.button_callback('{}'.format(user), user)
            key.append([button])
        key.append([back])
        if callback_id:
            button = bot.attach_buttons(key)
            upd = bot.send_answer_callback(callback_id, notification=None, attachments=button)
        else:
            upd = bot.send_buttons('Получатели', key, chat_id)
        mid = bot.get_message_id(upd)
    return mid


def add_consumer(user_id):
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO consumer (user_id) VALUES ({})".format(user_id))
        logger.info('New consumer {} added'.format(user_id))
        conn.commit()
    except Exception as e:
        logger.error('add_consumer: %s', e)
    c.close()


def del_consumer(user_id):
    c = conn.cursor()
    try:
        c.execute("DELETE FROM consumer WHERE user_id= {}".format(user_id))
        logger.info('Consumer {} deleted'.format(user_id))
        conn.commit()
    except Exception as e:
        logger.error('del_consumer: %s', e)
    c.close()


def get_consumers():
    c = conn.cursor()
    c.execute("SELECT user_id FROM consumer")
    lst = c.fetchall()
    dat = []
    if lst:
        dat = [lst[i][0] for i in range(len(lst))]
    c.close()
    return dat


def check_consumer(user_id):
    user_id = str(user_id)
    c = conn.cursor()
    c.execute("SELECT id FROM consumer WHERE user_id= {}".format(user_id))
    id = c.fetchall()
    if id:
        check = True
    else:
        check = False
    c.close()
    return check


def main():
    flag = None
    mid_m = None
    mid_d = None
    cmd = None
    while True:
        update = bot.get_updates()
        # chat_status_control()
        if update:
            user_id = bot.get_user_id(update)
            if check_consumer(user_id) or str(user_id) == admin:
                type_upd = bot.get_update_type(update)
                text = bot.get_text(update)
                chat_id = bot.get_chat_id(update)
                payload = bot.get_payload(update)
                cbid = bot.get_callback_id(update)
                if mid_m:
                    mid_d = mid_m
                if type_upd == 'bot_started':
                    mid_m = menu(callback_id=cbid, chat_id=chat_id)
                if type_upd == 'message_created':
                    if text.lower() == 'menu' or text.lower() == '/menu':
                        bot.delete_message(mid_d)
                        payload = 'home'
                    elif flag == 'addconsumer':
                        try:
                            user_id = text
                            flag = None
                        except:
                            user_id = None
                            flag = None
                        if user_id:
                            bot.delete_message(mid_d)
                            add_consumer(user_id)
                            bot.send_message('Получатель {} добавлен'.format(user_id), chat_id)
                            payload = 'home'
                        else:
                            payload = 'home'
                    else:
                        bot.delete_message(mid_d)
                        subscribe(text, chat_id)
                users = get_subscribe(chat_id)
                consumers = get_consumers()
                if not users:
                    list_users = []
                else:
                    list_users = users.split(' ')
                if not consumers:
                    list_consumer = []
                else:
                    list_consumer = consumers
                if payload == 'home':
                    mid_m = menu(callback_id=cbid, chat_id=chat_id)
                elif payload == 'subscribe':
                    bot.delete_message(mid_d)
                    bot.send_message('Отправьте имя пользователя для подписки', chat_id)
                elif payload == 'addconsumer' and str(user_id) == admin:
                    # bot.delete_message(mid_d)
                    bot.send_message('Отправьте user_id получателя', chat_id)
                    flag = 'addconsumer'
                elif payload == 'list' and not users:
                    menu(callback_id=cbid, chat_id=chat_id, notifi='У Вас нет подписок')
                elif payload == 'delconsumer' and not list_consumer and str(user_id) == admin:
                    menu(callback_id=cbid, chat_id=chat_id, notifi='Нет получателей')
                elif payload in list_users:
                    if cmd == 'unsubscribe':
                        notify = 'Вы отписались от {}'.format(payload)
                        mid_m = menu(callback_id=cbid, chat_id=chat_id, notifi=notify)
                        del_subscribe(payload, chat_id)
                    elif cmd:
                        mid_m = menu(callback_id=cbid, chat_id=chat_id, notifi='Жду команду')
                elif payload in list_consumer and str(user_id) == admin:
                    if cmd == 'delconsumer':
                        notify = 'Вы удалили получателя {}'.format(payload)
                        mid_m = menu(callback_id=cbid, chat_id=chat_id, notifi=notify)
                        del_consumer(payload)
                    elif cmd:
                        mid_m = menu(callback_id=cbid, chat_id=chat_id, notifi='Жду команду')
                elif payload == 'allunsubscribe':
                    menu(callback_id=cbid, chat_id=chat_id, notifi='Вы отписаны от всех пользователей')
                    del_all_subscribe(chat_id)
                    cmd = None
                elif payload == 'list' or payload == 'unsubscribe':
                    mid_m = list_subscribe(callback_id=cbid, chat_id=chat_id)
                    cmd = payload
                elif payload == 'delconsumer' and str(user_id) == admin:
                    mid_m = list_consumers(callback_id=cbid, chat_id=chat_id)
                    cmd = payload
                elif payload:
                    menu(callback_id=cbid, chat_id=chat_id, notifi='Доступ запрещён!')
            else:
                bot.send_message('Доступ запрещён!', chat_id=None, user_id=user_id)


def update_stories():
    while True:
        chats = get_list_chats()
        for chat in chats:
            if delay(chat):
                users = get_subscribe(chat)
                user = users.split(' ')
                start_download(user, chat)
                del_history(chat)
            time.sleep(1000)


update_thred = Thread(target=update_stories)
update_thred.start()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        exit()
