import datetime
import os
import json
import time

import requests
import argparse
import telegram
import sqlite3

from lxml import html


conn = sqlite3.connect('specials.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS specials 
(sp_date DATE, sp_name VARCHAR, UNIQUE(sp_date, sp_name))''')


def request_html_text(url, retries=50, wait=60):
    r = None
    for i in range(retries):
        if i == retries - 1:
            raise RuntimeError(f"Reached max request attempts ({retries}) for url {url}")
        try:
            r = requests.get(url)
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
            print(e)
        if r and r.ok:
            return r.text
        print(f"{datetime.datetime.now().isoformat()} | {r.status_code if r else 'No'} response. "
              f"Waiting {wait} seconds to retry. Attempt {i+1}/{retries}.")
        time.sleep(wait)


def parse_pizza_specials(html_text, pizza_spelling='pizza', date_section_index=2):
    doc = html.fromstring(html_text.encode('unicode-escape'))
    sections = doc.find_class('menu-section')
    if not sections:
        raise RuntimeError("Unable to parse specials menu.")

    # Scrape menu date
    menu_date = None
    section_0_titles = sections[0].find_class('menu-item-title')
    if section_0_titles and len(section_0_titles) == 3:
        menu_date = section_0_titles[date_section_index].text

    # Scrape pizza specials
    pizza_specials = []
    for s in sections[1:]:
        menu_section_title_div = s.find_class('menu-section-header')[0].find_class('menu-section-title')[0]
        special_category = menu_section_title_div.text
        if special_category.strip().lower() == pizza_spelling.lower():
            pizza_specials.extend([e.text for e in s.find_class('menu-item-title')])

    cache = {'date': menu_date, 'pizzas': pizza_specials}
    return cache


def print_summary(specials, date_str, vodka_is_special):
    print(f"Lil' Frankie's pizza specials on {date_str}:")
    for i, item in enumerate(specials):
        print(f"{i + 1}. {item}")
    print(f"Vodka pizza *{'IS' if vodka_is_special else 'IS NOT'}* on the specials menu for {date_str}")


def run(config, poll_interval=60):
    url = config['specials-menu-url']
    vodka_spelling = config['specials-menu-vodka-spelling']
    pizza_spelling = config['specials-menu-pizza-spelling']
    config_date_index = config['specials-menu-date-index']
    telegram_chat_ids_raw = config['telegram-chat-ids']
    telegram_bot_token = config['telegram-bot-token']

    telegram_bot = telegram.Bot(token=telegram_bot_token)

    telegram_chat_ids = None
    if telegram_chat_ids_raw:
        telegram_chat_ids = [x.strip() for x in telegram_chat_ids_raw.split(',')]

    prev_date = None
    while True:
        html_text = request_html_text(url)
        specials_cache = parse_pizza_specials(html_text, pizza_spelling, config_date_index)
        date_str = specials_cache['date']
        specials = specials_cache['pizzas']
        vodka_is_special = vodka_spelling.lower() in [s.strip().lower() for s in specials]

        if prev_date != date_str:
            print(f"{datetime.datetime.now().isoformat()} | Specials menu has been updated for {date_str}")

            sql_date = datetime.date.today().strftime('%Y-%m-%d')
            sql_rows = ((sql_date, special) for special in specials)
            c.executemany('''INSERT OR IGNORE INTO specials VALUES (?,?)''', sql_rows)
            conn.commit()

            print_summary(specials, date_str, vodka_is_special)
            if vodka_is_special and telegram_chat_ids:
                for chat_id in telegram_chat_ids:
                    message_text = f'{vodka_spelling} pizza is available at Lil Frankies tonight {date_str}'
                    telegram_bot.send_message(chat_id=chat_id, text=message_text)
            # elif telegram_chat_ids:
            #     for chat_id in telegram_chat_ids:
            #         message_text = f'{vodka_spelling} pizza is NOT available at Lil Frankies tonight {date_str}'
            #         telegram_bot.send_message(chat_id=chat_id, text=message_text)
            prev_date = date_str

        time.sleep(poll_interval)


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

    run(config)


if __name__ == '__main__':
    main()
