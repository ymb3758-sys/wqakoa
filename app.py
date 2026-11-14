import asyncio
import os
import threading

from flask import Flask

app = Flask(__name__)


def run_bot_polling():
    import bot

    asyncio.run(bot.main())


@app.route("/")
def index():
    return "NFT offer bot is running"


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    # Поднимаем поллер бота в отдельном потоке, чтобы держать процесс живым
    t = threading.Thread(target=run_bot_polling, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=port)