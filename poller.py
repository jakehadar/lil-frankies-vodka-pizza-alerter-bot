import os
import json
import time
import sqlite3
import datetime
import argparse
import itertools
import contextlib

# noinspection PyPackageRequirements
# telegram packaged with 'python-telegram-bot' in requirements.txt
import telegram
import requests
from lxml import html


class DatabaseWrapper:
    def __init__(self, database, schema):
        self.database = database
        self.schema = schema
        self._initialize()

    def _initialize(self):
        with open(self.schema, 'r') as schema:
            with contextlib.closing(sqlite3.connect(self.database)) as conn:
                with contextlib.closing(conn.cursor()) as c:
                    c.executescript(schema.read())

    def insert_specials(self, specials: [str]):
        with contextlib.closing(sqlite3.connect(self.database)) as conn:
            with contextlib.closing(conn.cursor()) as c:
                query = "INSERT OR IGNORE INTO specials (sp_name) VALUES (?)"
                c.executemany(query, [(x,) for x in specials])
                conn.commit()

    def update_subscriber(self, telegram_chat_id, is_subscribing):
        with contextlib.closing(sqlite3.connect(self.database)) as conn:
            with contextlib.closing(conn.cursor()) as c:
                query = """
                INSERT OR IGNORE INTO subscribers (telegram_chat_id, is_subscribing) VALUES (?1, NULL);
                UPDATE subscribers SET is_subscribing = ?2 WHERE telegram_chat_id = ?1; 
                """
                c.execute(query, (telegram_chat_id, int(is_subscribing)))

    def fetch_active_subscribers(self):
        with contextlib.closing(sqlite3.connect(self.database)) as conn:
            with contextlib.closing(conn.cursor()) as c:
                query = "SELECT telegram_chat_id FROM active_subscribers"
                subscribers = c.execute(query).fetchall()
                return list(itertools.chain(*subscribers))


class LilFrankiesVodkaPizzaSpecialAlerterBot:
    def __init__(self, config, database: DatabaseWrapper = None, telegram_bot: telegram.Bot = None):
        self.url = config['specials-menu-url']
        self.specials_menu_vodka_spelling = config['specials-menu-vodka-spelling']
        self.specials_menu_pizza_spelling = config['specials-menu-pizza-spelling']
        self.specials_menu_date_index = config['specials-menu-date-index']
        self.telegram_chat_ids = config['telegram-chat-ids']
        self.poller_retry_limit = config['poller-connection-retry-limit']
        self.poller_refresh_interval = config['poller-refresh-interval-seconds']

        self.database = database
        self.telegram = telegram_bot

    def request_html_text(self):
        def retries(count=0):
            while count < self.poller_retry_limit if self.poller_retry_limit else -1:
                yield count
                count += 1

        r = None
        for i in retries():
            try:
                r = requests.get(self.url)
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                print(e)
            if r and r.ok:
                return r.text
            print(f"{datetime.datetime.now().isoformat()} | Request attempt {i + 1} of "
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
        if self.database:
            stored_subscribers = self.database.fetch_active_subscribers()
            self.telegram_chat_ids = list(set(self.telegram_chat_ids) | set(stored_subscribers))

        prev_date = None
        while True:
            html_text = self.request_html_text()
            specials_cache = self.parse_pizza_specials(html_text)
            date_str = specials_cache['date']
            specials = specials_cache['pizzas']
            vodka_is_special = self.specials_menu_vodka_spelling.lower() in [s.strip().lower() for s in specials]

            if prev_date != date_str:
                self.database and self.database.insert_specials(specials)

                print(f"{datetime.datetime.now().isoformat()} | Specials menu has been updated for {date_str}")
                self.print_summary(specials, date_str, vodka_is_special)
                if vodka_is_special and self.telegram_chat_ids:
                    msg = f'{self.specials_menu_vodka_spelling} pizza is available at Lil Frankies tonight {date_str}'
                    for chat_id in self.telegram_chat_ids:
                        self.telegram.send_message(chat_id=chat_id, text=msg)
                prev_date = date_str

            time.sleep(self.poller_refresh_interval)


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
    telegram_chat_ids = config.get('telegram-chat-ids', [])
    if isinstance(telegram_chat_ids, str):
        telegram_chat_ids = [x.strip() for x in telegram_chat_ids.split(',')]
    config['telegram-chat-ids'] = telegram_chat_ids

    # Configure the bot
    bot = LilFrankiesVodkaPizzaSpecialAlerterBot(config)

    if config.get('sqlite-db-enabled'):
        db, schema = config.get('sqlite-db-filename'), config.get('sqlite-db-schema')
        bot.database = DatabaseWrapper(db, schema)

    if config.get('telegram-bot-token'):
        bot.telegram = telegram.Bot(token=config['telegram-bot-token'])

    bot.run()


if __name__ == '__main__':
    main()
