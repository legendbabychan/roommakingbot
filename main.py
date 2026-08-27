import os
import threading
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands

# --- RenderのダミーWebサーバー設定（スリープ防止・ポート監視用）---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    # Renderが指定するポート（既定値10000）でWebサーバーを起動
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# バックグラウンドでFlaskサーバーを動かす
threading.Thread(target=run_flask).start()

# --- 以降は元のBot処理 ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
TOKEN = os.environ.get("DISCORD_TOKEN")

# 閲覧権限の設定ヘルパー関数
def get_overwrites(guild, is_private, selected_members, exec_user):
    overwrites = {}
    if is_private:
        allowed_users = set(selected_members)
        allowed_users.add(exec_user)
        overwrites[guild.default_role] = discord.PermissionOverwrite(read_messages=False, connect=False)
        for user in allowed_users:
            overwrites[user] = discord.PermissionOverwrite(read_messages=True, connect=True)
    return overwrites


# ===================================================================
# 1. TRPG用 機能（/trpg）
# ===================================================================
class TRPGModal(discord.ui.Modal, title="TRPG部屋の作成設定"):
    session_name = discord.ui.TextInput(
        label="セッション名（カテゴリ名になります）",
        placeholder="例: クトゥルフ神話TRPG『邪神の呼び声』",
        required=True
    )
    date_str = discord.ui.TextInput(
        label="開催日",
        placeholder="例: 10/25 21:00〜",
        required=False
    )
    private_rooms = discord.ui.TextInput(
        label="GMと各人の個別TC/VCは必要ですか？",
        placeholder="必要なら「はい」、不要なら「いいえ」",
        default="いいえ",
        required=True
    )
    common_tcs = discord.ui.TextInput(
        label="共通TC（カンマ区切りで複数可）",
        placeholder="例: 連絡用, ダイス用（空欄なら1つの「共通TC」）",
        required=False,
        style=discord.TextStyle.paragraph
    )
    common_vcs = discord.ui.TextInput(
        label="共通VC（カンマ区切りで複数可）",
        placeholder="例: メインVC, 休憩所（空欄なら1つの「共通VC」）",
        required=False,
        style=discord.TextStyle.paragraph
    )

    def __init__(self, selected_members):
        super().__init__()
        self.selected_members = selected_members

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        gm_user = interaction.user

        allowed_users = set(self.selected_members)
        allowed_users.add(gm_user)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False, connect=False),
        }
        for user in allowed_users:
            overwrites[user] = discord.PermissionOverwrite(read_messages=True, connect=True)

        cat_name = f"🎲 {self.session_name.value}"
        category = await guild.create_category(name=cat_name, overwrites=overwrites)

        # 共通TC
        tc_input = self.common_tcs.value.strip()
        tc_names = [name.strip() for name in tc_input.split(",") if name.strip()] if tc_input else ["共通TC"]
        for tc_name in tc_names:
            await category.create_text_channel(name=tc_name)

        # 共通VC
        vc_input = self.common_vcs.value.strip()
        vc_names = [name.strip() for name in vc_input.split(",") if name.strip()] if vc_input else ["共通VC"]
        for vc_name in vc_names:
            await category.create_voice_channel(name=vc_name)

        # 個別（秘匿）TC・VC
        if "はい" in self.private_rooms.value or "yes" in self.private_rooms.value.lower():
            for member in self.selected_members:
                if member.bot or member == gm_user:
                    continue
                priv_overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False, connect=False),
                    member: discord.PermissionOverwrite(read_messages=True, connect=True),
                    gm_user: discord.PermissionOverwrite(read_messages=True, connect=True)
                }
                await category.create_text_channel(name=f"秘匿TC-{member.display_name}", overwrites=priv_overwrites)
                await category.create_voice_channel(name=f"秘匿VC-{member.display_name}", overwrites=priv_overwrites)

        await interaction.followup.send(
            f"✅ **GM: {gm_user.display_name}さん** のTRPGカテゴリ **【{cat_name}】** と各種チャンネルを作成しました！",
            ephemeral=True
        )


class TRPGSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.selected_members = []

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="参加プレイヤーとダイスBotを選択してください",
        min_values=1,
        max_values=25
    )
    async def select_users(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.selected_members = select.values
        names = ", ".join([m.display_name for m in self.selected_members])
        await interaction.response.edit_message(
            content=f"【TRPG部屋作成】\n選択中: **{names}**\n\n（※あなた自身はGMとして自動で権限が付与されます）\nメンバーを選び終わったら「詳細入力を開く」を押してください。",
            view=self
        )

    @discord.ui.button(label="詳細入力を開く", style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_members:
            await interaction.response.send_message("先にプレイヤー/Botを選択してください！", ephemeral=True)
            return
        await interaction.response.send_modal(TRPGModal(selected_members=self.selected_members))


# ===================================================================
# 2. カスタムマッチ用 機能（/custom）
# ===================================================================
class CustomModal(discord.ui.Modal, title="カスタム部屋の作成設定"):
    game_title = discord.ui.TextInput(
        label="ゲーム名・大会名（カテゴリ名になります）",
        placeholder="例: Valorant内戦 / 第1回Apexカスタム",
        required=True
    )
    date_str = discord.ui.TextInput(
        label="開催日",
        placeholder="例: 10/28 20:00〜",
        required=False
    )
    team_info = discord.ui.TextInput(
        label="チーム数 ＆ チーム別TCは必要か",
        placeholder="例: 「4, はい」または「4, いいえ」",
        default="4, いいえ",
        required=True
    )
    common_rooms = discord.ui.TextInput(
        label="共通TC・共通VC（カンマ区切りで入力）",
        placeholder="例: 配信連絡用, 集合VC",
        required=False,
        style=discord.TextStyle.paragraph
    )
    visibility = discord.ui.TextInput(
        label="参加者のみ閲覧可能にしますか？",
        placeholder="はい、またはいいえ",
        default="はい",
        required=True
    )

    def __init__(self, selected_members):
        super().__init__()
        self.selected_members = selected_members

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        exec_user = interaction.user

        is_private = "はい" in self.visibility.value or "yes" in self.visibility.value.lower()
        overwrites = get_overwrites(guild, is_private, self.selected_members, exec_user)

        cat_name = f"🏆 {self.game_title.value}"
        category = await guild.create_category(name=cat_name, overwrites=overwrites)

        raw_team_info = self.team_info.value.replace("，", ",").split(",")
        try:
            team_count = int(raw_team_info[0].strip())
        except ValueError:
            team_count = 2

        need_team_tc = False
        if len(raw_team_info) > 1 and ("はい" in raw_team_info[1] or "yes" in raw_team_info[1].lower()):
            need_team_tc = True

        common_input = self.common_rooms.value.strip()
        if common_input:
            room_names = [name.strip() for name in common_input.split(",") if name.strip()]
            for name in room_names:
                await category.create_text_channel(name=f"共通-{name}")
                await category.create_voice_channel(name=f"共通-{name}")
        else:
            await category.create_text_channel(name="全体アナウンス")
            await category.create_voice_channel(name="全体ロビー")

        for i in range(1, team_count + 1):
            await category.create_voice_channel(name=f"Team {i}")
            if need_team_tc:
                await category.create_text_channel(name=f"team-{i}-tc")

        await interaction.followup.send(
            f"✅ カスタム用カテゴリ **【{cat_name}】** と {team_count} チーム分のチャンネル作成が完了しました！",
            ephemeral=True
        )


class CustomSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.selected_members = []

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="参加プレイヤーと必要なBotを選択してください",
        min_values=1,
        max_values=25
    )
    async def select_users(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.selected_members = select.values
        names = ", ".join([m.display_name for m in self.selected_members])
        await interaction.response.edit_message(
            content=f"【カスタム部屋作成】\n選択中: **{names}**\n\nメンバーを選び終わったら「詳細入力を開く」を押してください。",
            view=self
        )

    @discord.ui.button(label="詳細入力を開く", style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_members:
            await interaction.response.send_message("先にプレイヤー/Botを選択してください！", ephemeral=True)
            return
        await interaction.response.send_modal(CustomModal(selected_members=self.selected_members))


# ===================================================================
# 3. 上映会用 機能（/movie）
# ===================================================================
class MovieModal(discord.ui.Modal, title="上映会部屋の作成設定"):
    title_name = discord.ui.TextInput(
        label="作品名・イベント名（カテゴリ名になります）",
        placeholder="例: 映画『〇〇』鑑賞会 / アニメ一気見会",
        required=True
    )
    date_str = discord.ui.TextInput(
        label="開催日時",
        placeholder="例: 11/01 21:00〜",
        required=False
    )
    tc_rooms = discord.ui.TextInput(
        label="作成するテキストチャット（カンマ区切り）",
        placeholder="例: 配信用チャット, ネタバレ感想用（空欄なら1つのチャット）",
        required=False,
        style=discord.TextStyle.paragraph
    )
    visibility = discord.ui.TextInput(
        label="参加者のみ閲覧可能にしますか？",
        placeholder="はい、またはいいえ",
        default="はい",
        required=True
    )

    def __init__(self, selected_members):
        super().__init__()
        self.selected_members = selected_members

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        exec_user = interaction.user

        is_private = "はい" in self.visibility.value or "yes" in self.visibility.value.lower()
        overwrites = get_overwrites(guild, is_private, self.selected_members, exec_user)

        cat_name = f"🎬 {self.title_name.value}"
        category = await guild.create_category(name=cat_name, overwrites=overwrites)

        # TC作成
        tc_input = self.tc_rooms.value.strip()
        tc_names = [name.strip() for name in tc_input.split(",") if name.strip()] if tc_input else ["上映会チャット"]
        for tc_name in tc_names:
            await category.create_text_channel(name=tc_name)

        # VC作成（作品名/イベント名をVC名に使用）
        vc_name = self.title_name.value
        await category.create_voice_channel(name=vc_name)

        await interaction.followup.send(
            f"✅ 上映会用カテゴリ **【{cat_name}】** を作成しました！",
            ephemeral=True
        )


class MovieSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.selected_members = []

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="参加メンバーを選択してください",
        min_values=1,
        max_values=25
    )
    async def select_users(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.selected_members = select.values
        names = ", ".join([m.display_name for m in self.selected_members])
        await interaction.response.edit_message(
            content=f"【上映会部屋作成】\n選択中: **{names}**\n\nメンバーを選び終わったら「詳細入力を開く」を押してください。",
            view=self
        )

    @discord.ui.button(label="詳細入力を開く", style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_members:
            await interaction.response.send_message("先にメンバーを選択してください！", ephemeral=True)
            return
        await interaction.response.send_modal(MovieModal(selected_members=self.selected_members))


# ===================================================================
# 4. その他・汎用 機能（/other）
# ===================================================================
class OtherModal(discord.ui.Modal, title="部屋の作成設定"):
    category_name = discord.ui.TextInput(
        label="部屋・プロジェクト名（カテゴリ名になります）",
        placeholder="例: マインクラフトサーバー / 雑談作業部屋",
        required=True
    )
    tc_rooms = discord.ui.TextInput(
        label="作成するテキストチャット（カンマ区切り）",
        placeholder="例: メモ, 画像共有（空欄なら1つのテキストチャット）",
        required=False,
        style=discord.TextStyle.paragraph
    )
    vc_rooms = discord.ui.TextInput(
        label="作成するボイスチャット（カンマ区切り）",
        placeholder="例: 通話部屋1, 通話部屋2（空欄なら1つのボイスチャット）",
        required=False,
        style=discord.TextStyle.paragraph
    )
    visibility = discord.ui.TextInput(
        label="参加者のみ閲覧可能にしますか？",
        placeholder="はい、またはいいえ",
        default="はい",
        required=True
    )

    def __init__(self, selected_members):
        super().__init__()
        self.selected_members = selected_members

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        exec_user = interaction.user

        is_private = "はい" in self.visibility.value or "yes" in self.visibility.value.lower()
        overwrites = get_overwrites(guild, is_private, self.selected_members, exec_user)

        cat_name = f"💬 {self.category_name.value}"
        category = await guild.create_category(name=cat_name, overwrites=overwrites)

        # TC作成
        tc_input = self.tc_rooms.value.strip()
        tc_names = [name.strip() for name in tc_input.split(",") if name.strip()] if tc_input else ["テキストチャット"]
        for tc_name in tc_names:
            await category.create_text_channel(name=tc_name)

        # VC作成
        vc_input = self.vc_rooms.value.strip()
        vc_names = [name.strip() for name in vc_input.split(",") if name.strip()] if vc_input else ["ボイスチャット"]
        for vc_name in vc_names:
            await category.create_voice_channel(name=vc_name)

        await interaction.followup.send(
            f"✅ カテゴリ **【{cat_name}】** を作成しました！",
            ephemeral=True
        )


class OtherSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.selected_members = []

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="参加メンバーを選択してください",
        min_values=1,
        max_values=25
    )
    async def select_users(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.selected_members = select.values
        names = ", ".join([m.display_name for m in self.selected_members])
        await interaction.response.edit_message(
            content=f"【部屋作成】\n選択中: **{names}**\n\nメンバーを選び終わったら「詳細入力を開く」を押してください。",
            view=self
        )

    @discord.ui.button(label="詳細入力を開く", style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_members:
            await interaction.response.send_message("先にメンバーを選択してください！", ephemeral=True)
            return
        await interaction.response.send_modal(OtherModal(selected_members=self.selected_members))


# ===================================================================
# 5. ヘルプ機能（/help）
# ===================================================================
@bot.tree.command(name="help", description="ボットの使い方とコマンド一覧を表示します")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 部屋作成Botの使い方",
        description="用途に合わせてスラッシュコマンド（`/`）を実行してください。\n選択したメンバー専用のカテゴリとチャンネルが自動作成されます。",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🎲 `/trpg`",
        value="TRPG用の部屋を作成します。\n共通の雑談・ダイスチャットや、プレイヤーごとの秘匿（個別）TC/VCを自動生成できます。",
        inline=False
    )
    embed.add_field(
        name="🏆 `/custom`",
        value="カスタムマッチ・内戦用の部屋を作成します。\n指定したチーム数のVCや、全体の連絡用チャットを自動生成できます。",
        inline=False
    )
    embed.add_field(
        name="🎬 `/movie`",
        value="上映会・観賞会用の部屋を作成します。\n作品名のVCや、感想・実況用のチャットを作成できます。",
        inline=False
    )
    embed.add_field(
        name="💬 `/other`",
        value="汎用・その他の部屋を作成します。\n自由な名前でテキスト・ボイスチャットをまとめたカテゴリを作成できます。",
        inline=False
    )
    embed.add_field(
        name="📄 詳細な説明書・仕様書",
        value="詳しいマニュアルや仕様は以下のWebページをご覧ください：\nここにURL",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ===================================================================
# 6. コマンド登録とBot起動
# ===================================================================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"ログインしました: {bot.user} (スラッシュコマンド同期完了)")
    print("---------------------------------")


if TOKEN:
    bot.run(TOKEN)
else:
    print("エラー: DISCORD_TOKEN が設定されていません。Renderの環境変数を確認してください。")