import sqlite3
from contextlib import contextmanager

from config import DATABASE_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recipients (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_id INTEGER NOT NULL,
                link TEXT DEFAULT '',
                slug TEXT DEFAULT '',
                amount TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resolutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                decision TEXT NOT NULL,
                decided_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (offer_id) REFERENCES offers(id)
            )
            """
        )


def ensure_user(user_id: int, name: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO recipients (user_id, name) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET name = excluded.name",
            (user_id, name),
        )


def get_recipients() -> list[tuple[int, str]]:
    with get_conn() as conn:
        return [
            (row["user_id"], row["name"])
            for row in conn.execute(
                "SELECT user_id, name FROM recipients ORDER BY name"
            )
        ]


def add_offer(
    recipient_id: int,
    link: str = "",
    slug: str = "",
    amount: str = "",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO offers (recipient_id, link, slug, amount) "
            "VALUES (?, ?, ?, ?)",
            (recipient_id, link, slug, amount),
        )
        return cur.lastrowid


def get_offer(offer_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM offers WHERE id = ?", (offer_id,)
        ).fetchone()


def record_resolution(
    offer_id: int, user_id: int, username: str | None, decision: str
):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO resolutions (offer_id, user_id, username, decision) "
            "VALUES (?, ?, ?, ?)",
            (offer_id, user_id, username, decision),
        )
        return cur.lastrowid


def get_resolutions(offer_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM resolutions WHERE offer_id = ?",
            (offer_id,),
        ).fetchall()