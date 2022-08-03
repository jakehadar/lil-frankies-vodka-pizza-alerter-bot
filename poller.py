import json

import requests
import argparse

from lxml import html


def request_html_text(url):
    r = requests.get(url)
    return r.text


def parse_specials_menu(html_text):
    doc = html.fromstring(html_text.encode('unicode-escape'))
    items = doc.find_class('menu-item')
    contents = [item.getchildren()[0].text for item in items]
    return contents


def run(config):
    config_date_index = config['specials-menu-date-index']
    vodka_spelling = config['specials-menu-match-str']
    html_text = request_html_text(config['specials-menu-url'])
    menu_items = parse_specials_menu(html_text)
    date_str = menu_items[config_date_index]
    specials = menu_items[config_date_index + 1:]
    vodka_is_special = vodka_spelling.lower() in [c.strip().lower() for c in specials]

    print(f"Lil' Frankie's specials on {date_str}:")
    for i, item in enumerate(specials):
        print(f"{i + 1}. {item}")
    print("")
    print(f"{vodka_spelling} pizza {'IS' if vodka_is_special else 'IS NOT'} on the specials menu for {date_str}")


def main():
    parser = argparse.ArgumentParser(description=r"Lil' Frankies' Vodka Pizza Alerter Bot")
    parser.add_argument("--config", type=str, help="Path to config file (json)", default="config.json")

    args = parser.parse_args()
    config = json.load(open(args.config, 'r'))

    run(config)


if __name__ == '__main__':
    main()
