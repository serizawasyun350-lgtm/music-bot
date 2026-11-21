import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
import yt_dlp
from keep_alive import keep_alive, bot_status

# Botの設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# 単曲ストリーミング取得用オプション
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt',
    # ↓追加：AndroidやWebクライアントを偽装してブロックを回避する
    'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
}

# プレイリスト高速展開用オプション
YTDL_PLAYLIST_OPTIONS = {
    'extract_flat': True,
    'quiet': True,
    'no_warnings': True,
    'playlistend': 50,
    'cookiefile': 'cookies.txt',
    # ↓追加：プレイリスト側も同様に設定
    'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# Webステータスを定期更新するタスク
@tasks.loop(seconds=15)
async def update_web_status():
    if bot.is_ready():
        bot_status["status"] = "オンライン"
        bot_status["name"] = str(bot.user)
        bot_status["guilds"] = len(bot.guilds)
        bot_status["latency"] = round(bot.latency * 1000)

        # ボイスチャンネルで再生中かチェック
        playing_titles = [vc.source.title for vc in bot.voice_clients if vc.is_playing() and hasattr(vc.source, 'title')]
        bot_status["playing"] = playing_titles[0] if playing_titles else "待機中"

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data)

@bot.event
async def on_ready():
    print(f'ログイン完了: {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        print(f'スラッシュコマンドを {len(synced)} 個同期しました')
    except Exception as e:
        print(f'コマンド同期エラー: {e}')
    
    if not update_web_status.is_running():
        update_web_status.start()

# /play コマンド（単曲・検索・プレイリスト対応 ＆ 読み込み中フリーズ防止対策）
@bot.tree.command(name="play", description="YouTubeのURL・プレイリスト・検索ワードで音楽を再生します")
@app_commands.describe(search="YouTubeのURL、プレイリストURL、または検索ワード")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ 先にボイスチャンネルに参加してください！", ephemeral=True)
        return

    # まず確実に「読み込み中」の状態を安全に受け付ける
    await interaction.response.defer(thinking=True)

    try:
        channel = interaction.user.voice.channel
        voice_client = interaction.guild.voice_client

        if not voice_client:
            voice_client = await channel.connect()
        elif voice_client.channel != channel:
            await voice_client.move_to(channel)

        queue = get_queue(interaction.guild.id)
        loop = asyncio.get_event_loop()

        # プレイリストURLかどうかの判定
        if "list=" in search and ("youtube.com" in search or "youtu.be" in search):
            data = await loop.run_in_executor(None, lambda: ytdl_playlist.extract_info(search, download=False))
            if 'entries' in data and data['entries']:
                added_count = 0
                for entry in data['entries']:
                    if not entry:
                        continue
                    video_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    title = entry.get('title') or "タイトル不明"
                    queue.append({"title": title, "url": video_url})
                    added_count += 1

                sync_web_status()
                playlist_title = data.get('title', 'YouTubeプレイリスト')
                await interaction.followup.send(f"📑 **プレイリスト追加:** `{playlist_title}` から **{added_count}曲** をキューに追加しました！")

                if not voice_client.is_playing() and not voice_client.is_paused():
                    await play_next(interaction.guild, interaction.channel)
                return

        # 単曲または検索ワードの処理
        info = await loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
        if 'entries' in info:
            info = info['entries'][0]

        track = {
            "title": info.get('title', search),
            "url": info.get('webpage_url') or search
        }
        queue.append(track)
        sync_web_status()

        if not voice_client.is_playing() and not voice_client.is_paused():
            await interaction.followup.send(f"🎶 **再生準備中:** `{track['title']}`")
            await play_next(interaction.guild, interaction.channel)
        else:
            await interaction.followup.send(f"➕ **キューに追加 ({len(queue)}番目):** `{track['title']}`")

    except Exception as e:
        # 万が一エラーが起きても、ここで必ず「読み込み中」を解除してエラー内容を伝える
        print(f"Playコマンドエラー: {e}")
        await interaction.followup.send(f"⚠️ エラーが発生しました:\n```{e}```")

# /stop コマンド
@bot.tree.command(name="stop", description="再生中の音楽を停止します")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        bot_status["playing"] = "待機中"
        await interaction.response.send_message("⏹️ 音楽を停止しました。")
    else:
        await interaction.response.send_message("❌ 現在再生中の音楽はありません。", ephemeral=True)

# /leave コマンド
@bot.tree.command(name="leave", description="Botをボイスチャンネルから切断します")
async def leave(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client:
        await voice_client.disconnect()
        bot_status["playing"] = "待機中"
        await interaction.response.send_message("👋 ボイスチャンネルから切断しました。")
    else:
        await interaction.response.send_message("❌ ボイスチャンネルに接続していません。", ephemeral=True)

# Webサーバー起動 & Bot起動
keep_alive()
TOKEN = os.environ.get("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("エラー: 環境変数 DISCORD_TOKEN が設定されていません。")
