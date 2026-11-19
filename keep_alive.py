import os
from threading import Thread

from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/")
def home():
    return jsonify(
        service="Agni Discord Bot",
        status="online",
        health="/health",
    )


@app.get("/health")
def health():
    return jsonify(status="ok"), 200


def run():
    port = int(os.environ.get("PORT", "8080"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


def keep_alive():
    # A daemon thread must never prevent the bot process from shutting down.
    thread = Thread(target=run, name="agni-health-server", daemon=True)
    thread.start()
