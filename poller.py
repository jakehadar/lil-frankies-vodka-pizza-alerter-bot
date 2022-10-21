import os
import json
import time
import sqlite3
import logging
import datetime
import argparse
import itertools
import contextlib

# noinspection PyPackageRequirements
# telegram packaged with 'python-telegram-bot' in requirements.txt
import pytz
import telegram
import telegram.ext
import requests
from lxml import html

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.DEBUG)
logger = logging.getLogger(__name__)


class DatabaseWrapper:
    def __init__(self, database, schema):
        self.database = database
        self.schema = schema
        self._initialize()

    def _initialize(self):
        with open(self.schema, 'r') as schema:
            with contextlib.closing(sqlite3.connect(self.database)) as c:
                c.executescript(schema.read())

    def insert_specials(self, specials: [str], date: str):
        with contextlib.closing(sqlite3.connect(self.database)) as c:
            query = "INSERT OR IGNORE INTO specials (sp_date, sp_name) VALUES (?, ?)"
            c.executemany(query, [(date, x) for x in specials])
            c.commit()

    def update_subscriber(self, telegram_chat_id, is_subscribing):
        with contextlib.closing(sqlite3.connect(self.database)) as c:
            try:
                query1 = "INSERT OR IGNORE INTO subscribers (telegram_chat_id, is_subscribing) VALUES (?, ?)"
                query2 = "UPDATE subscribers SET is_subscribing = ?2 WHERE telegram_chat_id = ?1"
                c.execute(query1, (telegram_chat_id, int(is_subscribing)))
                c.execute(query2, (telegram_chat_id, int(is_subscribing)))
                c.execute("COMMIT")
            except sqlite3.Error as e:
                c.execute("ROLLBACK")
                raise e

    def is_active_subscriber(self, telegram_chat_id) -> bool:
        with contextlib.closing(sqlite3.connect(self.database)) as c:
            query = "SELECT ? IN (SELECT telegram_chat_id FROM active_subscribers)"
            res = c.execute(query, (telegram_chat_id,)).fetchone()
            return bool(res[0])

    def get_active_subscribers(self) -> [str]:
        with contextlib.closing(sqlite3.connect(self.database)) as c:
            query = "SELECT telegram_chat_id FROM active_subscribers"
            res = c.execute(query).fetchall()
            return list(itertools.chain(*res)) or []

    def get_current_specials_date(self) -> datetime.date:
        with contextlib.closing(sqlite3.connect(self.database)) as c:
            query = "SELECT created FROM specials ORDER BY created DESC LIMIT 1"
            res = c.execute(query).fetchone()
            return datetime.datetime.strptime(res[0].split(' ')[0], '%Y-%m-%d').date()

    def get_current_specials_menu_date(self) -> str:
        with contextlib.closing(sqlite3.connect(self.database)) as c:
            query = """
            SELECT sp_date FROM specials
            WHERE created = (SELECT created FROM specials ORDER BY created DESC LIMIT 1)
            LIMIT 1
            """
            res = c.execute(query).fetchone()
            return res[0]

    def get_current_specials_names(self):
        with contextlib.closing(sqlite3.connect(self.database)) as c:
            query = """
            SELECT sp_name FROM specials
            WHERE created = (SELECT created FROM specials ORDER BY created DESC LIMIT 1)
            """
            res = c.execute(query).fetchall()
            return list(itertools.chain(*res))


class LilFrankiesVodkaPizzaSpecialAlerterBot:
    def __init__(self, config, database: DatabaseWrapper = None, telegram_bot: telegram.Bot = None,
                 telegram_updater: telegram.ext.Updater = None):
        # Configuration
        self.url = config['specials-menu-url']
        self.specials_menu_vodka_spelling = config['specials-menu-vodka-spelling']
        self.specials_menu_pizza_spelling = config['specials-menu-pizza-spelling']
        self.specials_menu_date_index = config['specials-menu-date-index']
        self.telegram_chat_ids = config['telegram-chat-ids']
        self.poller_retry_limit = config['poller-connection-retry-limit']
        self.poller_refresh_interval = config['poller-refresh-interval-seconds']

        # Dependencies
        self.database = database
        self.telegram_bot = telegram_bot
        self.telegram_updater = telegram_updater
        self.logger = logging.getLogger(self.__class__.__name__)

        # State
        self.is_running = False

    def subscribers(self):
        subscribers = self.database and self.database.get_active_subscribers()
        return subscribers or self.telegram_chat_ids or []

    def broadcast_to_subscribers(self, message):
        if self.telegram_bot:
            for chat_id in self.subscribers():
                self.telegram_bot.send_message(chat_id=chat_id, text=message)
                self.logger.debug(f'Sent "{message}" to {chat_id}')

    def request_html_text(self):
        def retries(count=0):
            while count < self.poller_retry_limit if self.poller_retry_limit else -1:
                yield count
                count += 1

        r = None
        for i in retries():
            try:
                self.logger.debug(f'requesting {self.url}')
                r = requests.get(self.url)
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                self.logger.exception(e)
            if r and r.ok:
                self.logger.debug(f'request ok')
                return r.text
            self.logger.info(f"Request attempt {i + 1} of "
                             f"{self.poller_retry_limit if self.poller_retry_limit else 'unlimited'}: "
                             f"{r.status_code if r else 'No'} response. "
                             f"Waiting {self.poller_refresh_interval} seconds before retrying...")
            time.sleep(self.poller_refresh_interval)
        raise RuntimeError(f"Reached maximum request attempts ({self.poller_retry_limit}) for url {self.url}")

    def parse_pizza_specials(self, html_text):
        doc = html.fromstring(html_text.encode('unicode-escape'))
        sections = doc.find_class('menu-section')
        if not sections:
            raise RuntimeError("Unable to parse specials menu.")

        # Scrape menu date
        menu_date = None
        section_0_titles = sections[0].find_class('menu-item-title')
        if section_0_titles and len(section_0_titles) == 3:
            menu_date = section_0_titles[self.specials_menu_date_index].text

        # Scrape pizza specials
        pizza_specials = []
        for s in sections[1:]:
            menu_section_title_div = s.find_class('menu-section-header')[0].find_class('menu-section-title')[0]
            special_category = menu_section_title_div.text
            if special_category.strip().lower() == self.specials_menu_pizza_spelling.lower():
                pizza_specials.extend([e.text for e in s.find_class('menu-item-title')])

        cache = {'date': menu_date, 'pizzas': pizza_specials}
        return cache

    @staticmethod
    def print_summary(specials, date_str, vodka_is_special):
        print(f"Lil' Frankie's pizza specials on {date_str}:")
        for i, item in enumerate(specials):
            print(f"{i + 1}. {item}")
        print(f"Vodka pizza *{'IS' if vodka_is_special else 'IS NOT'}* on the specials menu for {date_str}")

    def run(self):
        self.is_running = True
        self.telegram_updater and self.telegram_updater.start_polling()
        self.broadcast_to_subscribers("Bot started.")

        prev_date = None
        while self.is_running:
            html_text = self.request_html_text()
            specials_cache = self.parse_pizza_specials(html_text)
            date_str = specials_cache['date']
            specials = specials_cache['pizzas']
            vodka_is_special = self.specials_menu_vodka_spelling.lower() in [s.strip().lower() for s in specials]

            if prev_date != date_str:
                self.database and self.database.insert_specials(specials, date_str)

                self.logger.info(f"Specials menu has been updated for {date_str}")
                self.print_summary(specials, date_str, vodka_is_special)
                if vodka_is_special and self.telegram_chat_ids:
                    msg = f'{self.specials_menu_vodka_spelling} pizza is available at Lil Frankies tonight {date_str}'
                    self.broadcast_to_subscribers(msg)
                prev_date = date_str

            self.logger.debug(f'run loop will sleep for {self.poller_refresh_interval} seconds')
            time.sleep(self.poller_refresh_interval)

    def stop(self):
        self.is_running = False
        self.telegram_updater and self.telegram_updater.stop()
        self.broadcast_to_subscribers("Bot has been shut down.")


def main():
    parser = argparse.ArgumentParser(description=r"Lil' Frankies' Vodka Pizza Alerter Bot")
    parser.add_argument("--config", type=str, help="Path to config file (json)", default="config.json")
    parser.add_argument("--telegram-bot-token", type=str, help="Telegram api key (bot)",
                        default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    parser.add_argument("--telegram-chat-ids", type=str, help="Telegram chat id(s) to alert (comma separated)",
                        default=os.environ.get("TELEGRAM_CHAT_IDS"))

    args = parser.parse_args()
    config = json.load(open(args.config, 'r'))

    # Override certain JSON config with run arguments
    if args.telegram_bot_token:
        config['telegram-bot-token'] = args.telegram_bot_token
    if args.telegram_chat_ids:
        config['telegram-chat-ids'] = args.telegram_chat_ids

    # Parse optional telegram chat ids string into usable list
    telegram_chat_ids = config.get('telegram-chat-ids') or None
    if telegram_chat_ids and isinstance(telegram_chat_ids, str):
        telegram_chat_ids = [x.strip() for x in telegram_chat_ids.split(',')]
    config['telegram-chat-ids'] = telegram_chat_ids

    # Configure the bot
    bot = LilFrankiesVodkaPizzaSpecialAlerterBot(config)

    if config.get('sqlite-db-enabled'):
        db, schema = config['sqlite-db-filename'], config['sqlite-db-schema']
        bot.database = DatabaseWrapper(db, schema)

    if config.get('telegram-bot-token'):
        bot.telegram_bot = telegram.Bot(token=config['telegram-bot-token'])
        bot.telegram_updater = telegram.ext.Updater(token=config['telegram-bot-token'], use_context=True)

        # Callback function for handling User messages
        def subscriber(update: telegram.Update, context: telegram.ext.CallbackContext):
            is_active_subscriber = bot.database.is_active_subscriber(update.effective_chat.id)

            def footer_text(is_subscribed):
                return "Reply '{0}' to {1}, or 'specials' to see current daily specials.".format(
                    *('stop', 'unsubscribe from alerts') if is_subscribed else ('subscribe', 'opt-in to alerts'))

            # A) User is not subscribed and requests to opt in
            if not is_active_subscriber and update.message.text.lower().strip() in ['start', 'subscribe']:
                bot.database.update_subscriber(update.effective_chat.id, True)
                msg = f"You are now subscribed to Lil Frankies vodka pizza special alerts. " \
                      f"You will receive a text from this bot on evenings vodka pizza becomes " \
                      f"available on the specials menu. \n" \
                      f"\n" \
                      f"{footer_text(True)}"
                context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

            # B) User is subscribed and requests to opt out
            elif is_active_subscriber and update.message.text.lower().strip() in ['stop', 'unsubscribe']:
                bot.database.update_subscriber(update.effective_chat.id, False)
                msg = f"You are unsubscribed from alerts. " \
                      f"Text 'subscribe' to {context.bot.name} to opt-in to alerts again in the future."
                context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

            # C) User requests specials list
            elif update.message.text.lower().strip() == 'specials':
                tz = pytz.timezone('US/Eastern')
                sp_date = bot.database.get_current_specials_date()

                # Close of business for a special date is 2am the following day Sun-Thu,
                # and 4am the following day Fri-Sat.
                cob_date = sp_date + datetime.timedelta(days=1)
                cob_time = datetime.time(4 if sp_date.weekday() in [4, 5] else 2, tzinfo=tz)
                close_of_business = datetime.datetime.combine(cob_date, cob_time)

                # Only send the current specials if Lil Frankies is open
                if datetime.datetime.now(tz) < close_of_business:
                    weekday = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][
                        sp_date.weekday()]
                    month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][
                        sp_date.month - 1]
                    specials_text = '\n'.join(bot.database.get_current_specials_names())
                    msg = f"Pizza specials for {weekday} {month} {sp_date.day}, {sp_date.year}: \n" \
                          f"{specials_text}"
                else:
                    msg = "Today's pizza specials are not announced yet. Check again later this afternoon."
                msg = f"{msg}\n\n{footer_text(is_active_subscriber)}"
                context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

            # D) User sends something else and is already subscribed
            elif is_active_subscriber:
                context.bot.send_message(chat_id=update.effective_chat.id, text=footer_text(True))

            # E) User sends something else and is not already subscribed
            else:
                msg = "Hi, I can alert you when Vodka pizza becomes available at Lil Frankies. \n" \
                      "\n" \
                      f"{footer_text(False)}"
                context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

        subscriber_handler = telegram.ext.MessageHandler(telegram.ext.Filters.text, subscriber)
        bot.telegram_updater.dispatcher.add_handler(subscriber_handler)

    try:
        bot.run()
    except Exception as e:
        logger.exception(e)
    finally:
        bot.stop()


if __name__ == '__main__':
    main()
