from flask import Flask, render_template_string
from threading import Thread
import os

app = Flask('')

# Botのステータスと再生状況を保持する辞書（main.pyからリアルタイム更新）
bot_status = {
    "status": "起動中...",
    "name": "未接続",
    "guilds": 0,
    "latency": 0,
    "playing": "待機中",
    "queue": []  # 待機中の曲タイトルリスト
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="15">
    <title>Music Bot Status</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: #2c2f33;
            color: #ffffff;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }
        .card {
            background-color: #23272a;
            padding: 30px 35px;
            border-radius: 12px;
            box-shadow: 0 8px 16px rgba(0,0,0,0.4);
            text-align: center;
            max-width: 480px;
            width: 100%;
        }
        .status-badge {
            display: inline-block;
            padding: 6px 14px;
            border-radius: 20px;
            font-weight: bold;
            margin-bottom: 15px;
            background-color: {{ '#43b581' if data.status == 'オンライン' else '#faa61a' }};
            color: #fff;
        }
        h1 { margin: 0 0 20px; font-size: 24px; color: #7289da; }
        .info {
            text-align: left;
            background: #2c2f33;
            padding: 15px 18px;
            border-radius: 8px;
            margin-bottom: 15px;
        }
        .info p { margin: 8px 0; font-size: 14px; }
        .label { color: #99aab5; font-weight: bold; }
        .now-playing {
            background: #2c2f33;
            border-left: 4px solid #43b581;
            padding: 12px 15px;
            border-radius: 6px;
            text-align: left;
            margin-bottom: 15px;
        }
        .now-playing-title {
            font-size: 15px;
            font-weight: bold;
            color: #ffffff;
            margin-top: 4px;
            word-break: break-word;
        }
        .queue-box {
            text-align: left;
            background: #2c2f33;
            padding: 15px 18px;
            border-radius: 8px;
            max-height: 220px;
            overflow-y: auto;
        }
        .queue-list {
            margin: 8px 0 0;
            padding-left: 20px;
            font-size: 13px;
            color: #dcddde;
        }
        .queue-list li { margin-bottom: 6px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="status-badge">● {{ data.status }}</div>
        <h1>{{ data.name }}</h1>

        <div class="info">
            <p><span class="label">導入サーバー数:</span> {{ data.guilds }} サーバー</p>
            <p><span class="label">応答速度 (Ping):</span> {{ data.latency }} ms</p>
        </div>

        <div class="now-playing">
            <div class="label">🎵 現在の再生状況</div>
            <div class="now-playing-title">{{ data.playing }}</div>
        </div>

        <div class="queue-box">
            <div class="label">📋 再生待ちリスト ({{ data.queue|length }} 曲)</div>
            {% if data.queue %}
                <ol class="queue-list">
                {% for song in data.queue %}
                    <li>{{ song }}</li>
                {% endfor %}
                </ol>
            {% else %}
                <p style="font-size: 13px; color: #72767d; margin: 8px 0 0;">待機中の曲はありません</p>
            {% endif %}
        </div>

        <p style="font-size: 12px; color: #72767d; margin-top: 15px;">※15秒ごとに自動更新されます</p>
    </div>
</body>
</html>
"""

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return render_template_string(HTML_TEMPLATE, data=bot_status)

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
