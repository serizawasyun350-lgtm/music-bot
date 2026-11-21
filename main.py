import os
import asyncio
from collections import deque
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
    # ↓ ここを ios や tv に変更する
    'extractor_args': {'youtube': {'player_client': ['ios', 'tv', 'web']}},
}

# プレイリスト高速展開用オプション
YTDL_PLAYLIST_OPTIONS = {
    'extract_flat': True,
    'quiet': True,
    'no_warnings': True,
    'playlistend': 50,
    'cookiefile': 'cookies.txt',
    # ↓ 同様に変更する
    'extractor_args': {'youtube': {'player_client': ['ios', 'tv', 'web']}},
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
ytdl_playlist = yt_dlp.YoutubeDL(YTDL_PLAYLIST_OPTIONS)

# サーバーごとの再生キューと現在の再生曲を管理
guild_queues = {}
current_songs = {}

def get_queue(guild_id: int) -> deque:
    if guild_id not in guild_queues:
        guild_queues[guild_id] = deque()
    return guild_queues[guild_id]

def sync_web_status():
    """Webサーバー側のステータス辞書を最新状態に同期"""
    if bot.is_ready():
        bot_status["status"] = "オンライン"
        bot_status["name"] = str(bot.user)
        bot_status["guilds"] = len(bot.guilds)
        bot_status["latency"] = round(bot.latency * 1000)

    active_titles = [title for title in current_songs.values() if title]
    bot_status["playing"] = active_titles[0] if active_titles else "待機中"

    all_queued = []
    for q in guild_queues.values():
        for item in q:
            all_queued.append(item["title"])
    bot_status["queue"] = all_queued[:20]

@tasks.loop(seconds=10)
async def update_web_status_task():
    sync_web_status()

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
        if 'entries' in data:
            data = data['entries'][0]
        return cls(discord.FFmpegPCMAudio(data['url'], **FFMPEG_OPTIONS), data=data)

async def play_next(guild: discord.Guild, text_channel: discord.abc.Messageable = None):
    queue = get_queue(guild.id)
    voice_client = guild.voice_client

    if not voice_client or not voice_client.is_connected():
        current_songs.pop(guild.id, None)
        queue.clear()
        sync_web_status()
        return

    if not queue:
        current_songs.pop(guild.id, None)
        sync_web_status()
        return

    next_track = queue.popleft()
    current_songs[guild.id] = next_track["title"]
    sync_web_status()

    try:
        player = await YTDLSource.from_url(next_track["url"], loop=bot.loop)
        current_songs[guild.id] = player.title
        sync_web_status()

        def after_playing(error):
            if error:
                print(f"再生エラー: {error}")
            coro = play_next(guild, text_channel)
            asyncio.run_coroutine_threadsafe(coro, bot.loop)

        voice_client.play(player, after=after_playing)

        if text_channel:
            await text_channel.send(f"🎶 **次の曲を再生:** `{player.title}`")

    except Exception as e:
        print(f"トラック取得エラー ({next_track['title']}): {e}")
        if text_channel:
            await text_channel.send(f"⚠️ `{next_track['title']}` の読み込みに失敗したためスキップします。")
        await play_next(guild, text_channel)

@bot.event
async def on_ready():
    print(f'ログイン完了: {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        print(f'スラッシュコマンドを {len(synced)} 個同期しました')
    except Exception as e:
        print(f'コマンド同期エラー: {e}')

    if not update_web_status_task.is_running():
        update_web_status_task.start()
    sync_web_status()

# /play コマンド
@bot.tree.command(name="play", description="YouTubeのURL・プレイリスト・検索ワードで音楽を再生します")
@app_commands.describe(search="YouTubeのURL、プレイリストURL、または検索ワード")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ 先にボイスチャンネルに参加してください！", ephemeral=True)
        return

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

        # プレイリスト判定
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

        # 単曲・検索処理
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
        print(f"Playコマンドエラー: {e}")
        await interaction.followup.send(f"⚠️ エラーが発生しました:\n```{e}```")

# /nowplaying コマンド
@bot.tree.command(name="nowplaying", description="現在の再生状況と次の曲を表示します")
async def nowplaying(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    voice_client = interaction.guild.voice_client
    current = current_songs.get(guild_id)
    queue = get_queue(guild_id)

    embed = discord.Embed(title="🎵 現在の再生状況", color=discord.Color.green())

    if voice_client and voice_client.is_playing() and current:
        embed.add_field(name="▶️ 再生中", value=f"**{current}**", inline=False)
    elif voice_client and voice_client.is_paused() and current:
        embed.add_field(name="⏸️ 一時停止中", value=f"**{current}**", inline=False)
    else:
        embed.add_field(name="⏹️ ステータス", value="現在再生中の曲はありません", inline=False)

    if queue:
        next_song = queue[0]["title"]
        embed.add_field(name="⏭️ 次に再生", value=f"{next_song}（ほか待機 {len(queue) - 1} 曲）", inline=False)
    else:
        embed.add_field(name="📋 待機リスト", value="なし", inline=False)

    await interaction.response.send_message(embed=embed)

# /queue コマンド
@bot.tree.command(name="queue", description="現在の再生待ちリスト（キュー）を表示します")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    current = current_songs.get(guild_id)
    queue = get_queue(guild_id)

    embed = discord.Embed(title="📋 再生待ちリスト (キュー)", color=discord.Color.blurple())
    embed.add_field(name="🎶 現在再生中", value=current if current else "なし", inline=False)

    if queue:
        lines = [f"`{i+1}.` {item['title']}" for i, item in enumerate(list(queue)[:15])]
        if len(queue) > 15:
            lines.append(f"...ほか **{len(queue) - 15}曲**")
        embed.add_field(name=f"待機中 ({len(queue)}曲)", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="待機中", value="キューは空です", inline=False)

    await interaction.response.send_message(embed=embed)

# /skip コマンド
@bot.tree.command(name="skip", description="再生中の曲をスキップして次の曲を再生します")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
        await interaction.response.send_message("⏭️ 曲をスキップしました。")
    else:
        await interaction.response.send_message("❌ スキップできる曲が再生されていません。", ephemeral=True)

# /stop コマンド
@bot.tree.command(name="stop", description="再生を停止し、キューをすべて削除します")
async def stop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    voice_client = interaction.guild.voice_client
    queue = get_queue(guild_id)
    queue.clear()
    current_songs.pop(guild_id, None)
    sync_web_status()

    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
        await interaction.response.send_message("⏹️ 再生を停止し、待機リストを空にしました。")
    else:
        await interaction.response.send_message("🗑️ 待機リストを空にしました。")

# /leave コマンド
@bot.tree.command(name="leave", description="Botをボイスチャンネルから切断します")
async def leave(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    voice_client = interaction.guild.voice_client
    get_queue(guild_id).clear()
    current_songs.pop(guild_id, None)
    sync_web_status()

    if voice_client:
        await voice_client.disconnect()
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
