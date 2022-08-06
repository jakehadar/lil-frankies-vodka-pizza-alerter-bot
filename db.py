def init_tables(con):
    cur = con.cursor()
    try:
        res = cur.execute("""SELECT name FROM sqlite_schema WHERE type='table'""").fetchall()
        existing_tables = list(map(lambda x: x[0], res))
        if 'subscribers' not in existing_tables:
            cur.execute('''CREATE TABLE subscribers (telegram_id text, subscribed bool)''')
            con.commit()
            print("Created table 'subscribers'")

        if 'specials' not in existing_tables:
            cur.execute('''CREATE TABLE subscribers (telegram_id text, subscribed bool)''')
            con.commit()
            print("Created table 'specials'")

    except Exception as e:
        raise e
    finally:
        cur.close()


def get_subscribers(con):
    cur = con.cursor()
    try:
        res = cur.execute('''SELECT telegram_id, subscribed FROM subscribers''')
        con.commit()
    except Exception as e:
        raise e
    finally:
        cur.close()
    return res


def create_subscriber(con, telegram_id):
    current_subscribers = map(lambda x: x[0], get_subscribers(con))
    if telegram_id in current_subscribers:
        return
    cur = con.cursor()
    try:
        cur.execute('''INSERT INTO subscribers VALUES (?, ?)''', telegram_id, True)
        con.commit()
    except Exception as e:
        raise e
    finally:
        cur.close()


def unsubscribe(con, telegram_id):
    cur = con.cursor()
    try:
        cur.execute('''UPDATE subscribers SET subscribed = FALSE WHERE telegram_id = ?''', telegram_id)
        con.commit()
    except Exception as e:
        raise e
    finally:
        cur.close()


def insert_specials(con, specials_data):
    cur = con.cursor()
    try:
        cur.execute("""INSERT INTO specials VALUES (datetime('now'), ?, ?)""", specials_data)
        con.commit()
    except Exception as e:
        raise e
    finally:
        cur.close()