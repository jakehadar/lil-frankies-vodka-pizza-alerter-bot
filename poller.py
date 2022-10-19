import os
import json
import time
import datetime
import contextlib

import requests
import argparse
import telegram
import sqlite3

from lxml import html


class LilFrankiesVodkaPizzaSpecialAlerterBot:
    def __init__(self, config):
        self.url = config['specials-menu-url']
        self.vodka_spelling = config['specials-menu-vodka-spelling']
        self.pizza_spelling = config['specials-menu-pizza-spelling']
        self.html_menu_date_index = config['specials-menu-date-index']
        self.telegram_chat_ids_raw = config['telegram-chat-ids']
        self.telegram_bot_token = config['telegram-bot-token']
        self.connection_retry_limit = config['poller-connection-retry-limit']
        self.poller_refresh_interval = config['poller-refresh-interval-seconds']
        self.db_enabled = config['sqlite-db-enabled']
        self.db_filename = config['sqlite-db-filename']

        if self.db_enabled:
            with contextlib.closing(sqlite3.connect(self.db_filename)) as conn:
                with contextlib.closing(conn.cursor()) as c:
                    c.execute("""CREATE TABLE IF NOT EXISTS specials 
                                 (sp_date DATE, sp_name VARCHAR, UNIQUE(sp_date, sp_name))""")
                    c.execute("""CREATE VIEW IF NOT EXISTS vodka_special_dates 
                                 AS SELECT sp_date, sp_name FROM specials WHERE sp_name LIKE '%Vodka%'""")

    def request_html_text(self):
        def retries(count=0):
            while count < self.connection_retry_limit if self.connection_retry_limit else -1:
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
            print(f"{datetime.datetime.now().isoformat()} | Request attempt {i+1} of "
                  f"{self.connection_retry_limit if self.connection_retry_limit else 'unlimited'}: "
                  f"{r.status_code if r else 'No'} response. "
                  f"Waiting {self.poller_refresh_interval} seconds before retrying...")
            time.sleep(self.poller_refresh_interval)
        raise RuntimeError(f"Reached maximum request attempts ({self.connection_retry_limit}) for url {self.url}")

    def parse_pizza_specials(self, html_text):
        doc = html.fromstring(html_text.encode('unicode-escape'))
        sections = doc.find_class('menu-section')
        if not sections:
            raise RuntimeError("Unable to parse specials menu.")

        # Scrape menu date
        menu_date = None
        section_0_titles = sections[0].find_class('menu-item-title')
        if section_0_titles and len(section_0_titles) == 3:
            menu_date = section_0_titles[self.html_menu_date_index].text

        # Scrape pizza specials
        pizza_specials = []
        for s in sections[1:]:
            menu_section_title_div = s.find_class('menu-section-header')[0].find_class('menu-section-title')[0]
            special_category = menu_section_title_div.text
            if special_category.strip().lower() == self.pizza_spelling.lower():
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
        telegram_bot = telegram.Bot(token=self.telegram_bot_token)

        telegram_chat_ids = None
        if self.telegram_chat_ids_raw:
            telegram_chat_ids = [x.strip() for x in self.telegram_chat_ids_raw.split(',')]

        prev_date = None
        while True:
            html_text = self.request_html_text()
            specials_cache = self.parse_pizza_specials(html_text)
            date_str = specials_cache['date']
            specials = specials_cache['pizzas']
            vodka_is_special = self.vodka_spelling.lower() in [s.strip().lower() for s in specials]

            if prev_date != date_str:
                if self.db_enabled:
                    sql_date = datetime.date.today().strftime('%Y-%m-%d')
                    sql_rows = ((sql_date, special) for special in specials)
                    with contextlib.closing(sqlite3.connect(self.db_filename)) as conn:
                        with contextlib.closing(conn.cursor()) as c:
                            c.executemany('''INSERT OR IGNORE INTO specials VALUES (?,?)''', sql_rows)
                            conn.commit()

                print(f"{datetime.datetime.now().isoformat()} | Specials menu has been updated for {date_str}")
                self.print_summary(specials, date_str, vodka_is_special)
                if vodka_is_special and telegram_chat_ids:
                    for chat_id in telegram_chat_ids:
                        message_text = f'{self.vodka_spelling} pizza is available at Lil Frankies tonight {date_str}'
                        telegram_bot.send_message(chat_id=chat_id, text=message_text)
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

    if args.telegram_chat_ids:
        config['telegram-chat-ids'] = args.telegram_chat_ids

    if args.telegram_bot_token:
        config['telegram-bot-token'] = args.telegram_bot_token

    bot = LilFrankiesVodkaPizzaSpecialAlerterBot(config)
    bot.run()


if __name__ == '__main__':
    main()
