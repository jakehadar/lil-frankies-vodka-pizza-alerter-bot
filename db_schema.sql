BEGIN;
CREATE TABLE IF NOT EXISTS specials
(
    created TEXT,
    sp_date TEXT,
    sp_name TEXT,
    UNIQUE (sp_date, sp_name)
);
CREATE TABLE IF NOT EXISTS subscribers
(
    telegram_chat_id INTEGER PRIMARY KEY,
    is_subscribing   INTEGER,
    UNIQUE (telegram_chat_id)
);
CREATE TABLE IF NOT EXISTS subscriber_logs
(
    trigger_time       TEXT,
    trigger_type       TEXT,
    telegram_chat_id   INTEGER,
    old_is_subscribing INTEGER,
    new_is_subscribing INTEGER
);
CREATE TRIGGER IF NOT EXISTS set_special_created_time_after_insert
    AFTER INSERT
    ON specials
    FOR EACH ROW
    WHEN (new.created IS NULL)
BEGIN
    UPDATE specials SET created = DATETIME('NOW') WHERE ROWID = new.ROWID;
END;
CREATE TRIGGER IF NOT EXISTS log_subscriber_after_create
    AFTER INSERT
    ON subscribers
BEGIN
    INSERT INTO subscriber_logs (trigger_time, trigger_type, telegram_chat_id, old_is_subscribing, new_is_subscribing)
    VALUES (DATETIME('NOW'), 'INSERT', new.telegram_chat_id, NULL, new.is_subscribing);
END;
CREATE TRIGGER IF NOT EXISTS log_subscriber_after_update
    AFTER UPDATE
    ON subscribers
    WHEN old.is_subscribing <> new.is_subscribing
BEGIN
    INSERT INTO subscriber_logs (trigger_time, trigger_type, telegram_chat_id, old_is_subscribing, new_is_subscribing)
    VALUES (DATETIME('NOW'), 'UPDATE', new.telegram_chat_id, old.is_subscribing, new.is_subscribing);
END;
CREATE TRIGGER IF NOT EXISTS log_subscriber_after_delete
    AFTER DELETE
    ON subscribers
BEGIN
    INSERT INTO subscriber_logs (trigger_time, trigger_type, telegram_chat_id, old_is_subscribing, new_is_subscribing)
    VALUES (DATETIME('NOW'), 'DELETE', old.telegram_chat_id, old.is_subscribing, NULL);
END;
CREATE VIEW IF NOT EXISTS active_subscribers AS
SELECT telegram_chat_id
FROM subscribers
WHERE is_subscribing = 1;
CREATE VIEW IF NOT EXISTS vodka_special_dates AS
SELECT sp_date, sp_name
FROM specials
WHERE sp_name LIKE '%Vodka%';
COMMIT;