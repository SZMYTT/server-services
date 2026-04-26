# bot/discord_bot.py
# Prisma — SystemOS Discord Bot
# Friendly, warm, helpful colleague across all workspaces.
# Hosted on Lenovo server, runs as systemd service.

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import logging
from dotenv import load_dotenv
import os
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.queue import add_task, get_task_status
from services.notifier import notify_loop
from services.analytics import log_command
from bot.permissions import (
    get_workspace_from_channel,
    get_user_language,
    is_operator
)
from bot.responses import r
from bot.embeds import (
    embed_queued, embed_success, embed_info, embed_error,
    embed_declined, embed_operator_alert,
)

load_dotenv()
logger = logging.getLogger("prisma")

# ── Bot setup ────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    description="Prisma — your SystemOS assistant"
)

# ── Events ───────────────────────────────────────────────────

@bot.event
async def on_ready():
    guild = discord.Object(id=1495026378217885726)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)
    bot.loop.create_task(notify_loop(bot))
    print(f"✓ Prisma is online as {bot.user}")
    print(f"✓ Synced {len(synced)} commands")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Command error: {error}")

# ── Slash commands ───────────────────────────────────────────

@bot.tree.command(
    name="research",
    description="Queue a research task for your workspace"
)
@app_commands.describe(topic="What would you like researched?")
async def research(interaction: discord.Interaction, topic: str):

    lang = get_user_language(interaction.user.name)
    workspace = get_workspace_from_channel(interaction.channel.name)

    if not workspace:
        await interaction.response.send_message(
            r("wrong_channel", lang), ephemeral=True
        )
        return

    task_id = await add_task(
        workspace=workspace,
        user=interaction.user.name,
        task_type="research",
        risk_level="internal",
        module="research",
        input=topic,
        trigger_type="discord"
    )

    await interaction.response.send_message(
        embed=embed_queued(
            title=r("research_queued_title", lang),
            description=r("research_queued_body", lang).format(topic=topic),
            task_id=task_id,
            lang=lang,
            risk_level="internal",
        )
    )

    await notify_operator(
        bot=bot,
        task_id=task_id,
        workspace=workspace,
        user=interaction.user.name,
        task_type="research",
        risk_level="internal",
        input=topic
    )

    await log_command("research", workspace, interaction.user.name)


@bot.tree.command(
    name="summary",
    description="Request a business summary"
)
@app_commands.describe(
    period="Time period: today, week, or month"
)
@app_commands.choices(period=[
    app_commands.Choice(name="Today", value="today"),
    app_commands.Choice(name="This week", value="week"),
    app_commands.Choice(name="This month", value="month"),
])
async def summary(interaction: discord.Interaction, period: str):

    lang = get_user_language(interaction.user.name)
    workspace = get_workspace_from_channel(interaction.channel.name)

    if not workspace:
        await interaction.response.send_message(
            r("wrong_channel", lang), ephemeral=True
        )
        return

    task_id = await add_task(
        workspace=workspace,
        user=interaction.user.name,
        task_type="finance",
        risk_level="internal",
        module="finance",
        input=f"Generate {period} summary",
        trigger_type="discord"
    )

    await interaction.response.send_message(
        embed=embed_queued(
            title=r("summary_queued_title", lang),
            description=r("summary_queued_body", lang).format(period=period),
            task_id=task_id,
            lang=lang,
            risk_level="internal",
        )
    )

    await notify_operator(
        bot=bot,
        task_id=task_id,
        workspace=workspace,
        user=interaction.user.name,
        task_type="finance",
        risk_level="internal",
        input=f"{period} summary"
    )


@bot.tree.command(
    name="content",
    description="Queue a content creation task"
)
@app_commands.describe(brief="Describe what content you need")
async def content(interaction: discord.Interaction, brief: str):

    lang = get_user_language(interaction.user.name)
    workspace = get_workspace_from_channel(interaction.channel.name)

    if not workspace:
        await interaction.response.send_message(
            r("wrong_channel", lang), ephemeral=True
        )
        return

    task_id = await add_task(
        workspace=workspace,
        user=interaction.user.name,
        task_type="content",
        risk_level="public",       # content is public risk
        module="content",
        input=brief,
        trigger_type="discord"
    )

    await interaction.response.send_message(
        embed=embed_queued(
            title=r("content_queued_title", lang),
            description=r("content_queued_body", lang).format(brief=brief),
            task_id=task_id,
            lang=lang,
            risk_level="public",
        )
    )

    await notify_operator(
        bot=bot,
        task_id=task_id,
        workspace=workspace,
        user=interaction.user.name,
        task_type="content",
        risk_level="public",
        input=brief
    )


@bot.tree.command(
    name="messages",
    description="See pending customer messages with reply drafts"
)
async def messages(interaction: discord.Interaction):

    lang = get_user_language(interaction.user.name)
    workspace = get_workspace_from_channel(interaction.channel.name)

    if not workspace:
        await interaction.response.send_message(
            r("wrong_channel", lang), ephemeral=True
        )
        return

    task_id = await add_task(
        workspace=workspace,
        user=interaction.user.name,
        task_type="comms",
        risk_level="financial",    # replies have real-world consequences
        module="customer_comms",
        input="Fetch pending messages and draft replies",
        trigger_type="discord"
    )

    await interaction.response.send_message(
        embed=embed_queued(
            title=r("messages_queued_title", lang),
            description=r("messages_queued_body", lang),
            task_id=task_id,
            lang=lang,
            risk_level="financial",
        )
    )

    await notify_operator(
        bot=bot,
        task_id=task_id,
        workspace=workspace,
        user=interaction.user.name,
        task_type="comms",
        risk_level="financial",
        input="Fetch pending messages"
    )


@bot.tree.command(
    name="stock",
    description="Check current stock levels and alerts"
)
async def stock(interaction: discord.Interaction):

    lang = get_user_language(interaction.user.name)
    workspace = get_workspace_from_channel(interaction.channel.name)

    if not workspace:
        await interaction.response.send_message(
            r("wrong_channel", lang), ephemeral=True
        )
        return

    if workspace not in ["candles"]:
        await interaction.response.send_message(
            r("not_available", lang), ephemeral=True
        )
        return

    task_id = await add_task(
        workspace=workspace,
        user=interaction.user.name,
        task_type="finance",
        risk_level="internal",
        module="inventory",
        input="Check current stock levels",
        trigger_type="discord"
    )

    await interaction.response.send_message(
        embed=embed_queued(
            title=r("stock_queued_title", lang),
            description=r("stock_queued_body", lang),
            task_id=task_id,
            lang=lang,
            risk_level="internal",
        )
    )

    await notify_operator(
        bot=bot,
        task_id=task_id,
        workspace=workspace,
        user=interaction.user.name,
        task_type="finance",
        risk_level="internal",
        input="Stock check"
    )


@bot.tree.command(
    name="auctions",
    description="See today's auction scan results"
)
async def auctions(interaction: discord.Interaction):

    lang = get_user_language(interaction.user.name)
    workspace = get_workspace_from_channel(interaction.channel.name)

    if workspace not in ["cars"]:
        await interaction.response.send_message(
            r("not_available", lang), ephemeral=True
        )
        return

    task_id = await add_task(
        workspace=workspace,
        user=interaction.user.name,
        task_type="research",
        risk_level="internal",
        module="auction_sourcing",
        input="Fetch today's auction listings",
        trigger_type="discord"
    )

    await interaction.response.send_message(
        embed=embed_queued(
            title=r("auctions_queued_title", lang),
            description=r("auctions_queued_body", lang),
            task_id=task_id,
            lang=lang,
            risk_level="internal",
        )
    )

    await notify_operator(
        bot=bot,
        task_id=task_id,
        workspace=workspace,
        user=interaction.user.name,
        task_type="research",
        risk_level="internal",
        input="Auction scan"
    )


@bot.tree.command(
    name="status",
    description="Check your queued and running tasks"
)
async def status(interaction: discord.Interaction):

    lang = get_user_language(interaction.user.name)
    workspace = get_workspace_from_channel(interaction.channel.name)

    tasks = await get_task_status(
        workspace=workspace,
        user=interaction.user.name
    )

    if not tasks:
        await interaction.response.send_message(
            embed=embed_info(r("no_tasks", lang)),
            ephemeral=True
        )
        return

    lines = []
    for t in tasks:
        status_emoji = {
            "queued": "🕐",
            "pending_approval": "⏳",
            "approved": "✅",
            "running": "⚡",
            "done": "✓",
            "failed": "✗",
            "declined": "✗"
        }.get(t["status"], "•")

        lines.append(
            f"{status_emoji} `{t['id'][:8]}` — "
            f"**{t['task_type']}** — {t['status']}"
        )

    await interaction.response.send_message(
        embed=info_embed("\n".join(lines)),
        ephemeral=True
    )


# ── Operator-only commands ───────────────────────────────────

@bot.tree.command(
    name="approve",
    description="[Operator] Approve a queued task"
)
@app_commands.describe(task_id="Task ID to approve")
async def approve(interaction: discord.Interaction, task_id: str):

    if not is_operator(interaction.user.name):
        await interaction.response.send_message(
            "This command is for Daniel only.", ephemeral=True
        )
        return

    from services.queue import approve_task
    await approve_task(task_id, approved_by="daniel")

    await interaction.response.send_message(
        embed=embed_success(f"Task `{task_id[:8]}` approved. Running now.")
    )


@bot.tree.command(
    name="decline",
    description="[Operator] Decline a task with a reason"
)
@app_commands.describe(
    task_id="Task ID to decline",
    reason="Reason shown to the user"
)
async def decline(
    interaction: discord.Interaction,
    task_id: str,
    reason: str
):
    if not is_operator(interaction.user.name):
        await interaction.response.send_message(
            "This command is for Daniel only.", ephemeral=True
        )
        return

    from services.queue import decline_task, get_task
    await decline_task(task_id, reason=reason)

    # Notify the user in their workspace channel
    task = await get_task(task_id)
    if task:
        lang = get_user_language(task["user_name"])
        workspace_channel = f"{task['workspace']}-commands"
        channel = discord.utils.get(
            bot.get_all_channels(),
            name=workspace_channel
        )
        if channel:
            await channel.send(
                embed=declined_embed(
                    task_id=task_id,
                    reason=reason,
                    lang=lang
                )
            )

    await interaction.response.send_message(
        embed=embed_info(
            f"Task `{task_id[:8]}` declined. "
            f"User notified with reason."
        ),
        ephemeral=True
    )


@bot.tree.command(
    name="publish",
    description="[Operator] Approve completed content to post publicly"
)
@app_commands.describe(task_id="Task ID to publish")
async def publish(interaction: discord.Interaction, task_id: str):

    if not is_operator(interaction.user.name):
        await interaction.response.send_message(
            "This command is for Daniel only.", ephemeral=True
        )
        return

    from services.queue import publish_task
    await publish_task(task_id)

    await interaction.response.send_message(
        embed=embed_success(
            f"Task `{task_id[:8]}` approved for publishing. Posting now."
        )
    )


@bot.tree.command(
    name="queue",
    description="[Operator] View full task queue across all workspaces"
)
async def queue_view(interaction: discord.Interaction):

    if not is_operator(interaction.user.name):
        await interaction.response.send_message(
            "This command is for Daniel only.", ephemeral=True
        )
        return

    from services.queue import get_full_queue
    tasks = await get_full_queue()

    if not tasks:
        await interaction.response.send_message(
            embed=info_embed("Queue is empty."),
            ephemeral=True
        )
        return

    lines = []
    for t in tasks[:10]:  # Discord embed limit
        risk_emoji = {
            "internal": "⚪",
            "public": "🟡",
            "financial": "🔴"
        }.get(t["risk_level"], "⚪")

        lines.append(
            f"{risk_emoji} `{t['id'][:8]}` "
            f"**{t['workspace']}** · {t['task_type']} · "
            f"{t['user_name']} · {t['status']}"
        )

    await interaction.response.send_message(
        embed=embed_info("\n".join(lines)),
        ephemeral=True
    )


# ── Helper functions ─────────────────────────────────────────

async def notify_operator(
    bot, task_id, workspace, user, task_type, risk_level, input
):
    """Ping Daniel in #operator-log when a new task is submitted."""

    operator_channel = discord.utils.get(
        bot.get_all_channels(),
        name="operator-log"
    )

    if not operator_channel:
        return

    risk_emoji = {
        "internal": "⚪",
        "public": "🟡",
        "financial": "🔴"
    }.get(risk_level, "⚪")

    await operator_channel.send(
        content="<@DANIEL_DISCORD_ID>",
        embed=embed_operator_alert(
            task_id=task_id,
            workspace=workspace,
            user=user,
            task_type=task_type,
            risk_level=risk_level,
            input_text=input,
        ),
        view=ApprovalView(task_id=task_id)
    )


class ApprovalView(discord.ui.View):
    """Approve / Decline buttons on operator-log notifications."""

    def __init__(self, task_id: str):
        super().__init__(timeout=None)
        self.task_id = task_id

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        emoji="✅"
    )
    async def approve_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not is_operator(interaction.user.name):
            await interaction.response.send_message(
                "Only Daniel can approve tasks.", ephemeral=True
            )
            return

        from services.queue import approve_task
        await approve_task(self.task_id, approved_by="daniel")

        # Disable buttons after action
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            embed=success_embed(
                f"Task `{self.task_id[:8]}` approved. Running now."
            ),
            ephemeral=True
        )

    @discord.ui.button(
        label="Decline",
        style=discord.ButtonStyle.danger,
        emoji="✗"
    )
    async def decline_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not is_operator(interaction.user.name):
            await interaction.response.send_message(
                "Only Daniel can decline tasks.", ephemeral=True
            )
            return

        # Open a modal to get decline reason
        await interaction.response.send_modal(
            DeclineModal(task_id=self.task_id, view=self)
        )


class DeclineModal(discord.ui.Modal, title="Decline reason"):
    """Modal popup for Daniel to enter decline reason."""

    reason = discord.ui.TextInput(
        label="Reason (shown to the user)",
        placeholder="e.g. Too similar to last week's research",
        min_length=5,
        max_length=200
    )

    def __init__(self, task_id: str, view: ApprovalView):
        super().__init__()
        self.task_id = task_id
        self.approval_view = view

    async def on_submit(self, interaction: discord.Interaction):
        from services.queue import decline_task, get_task
        await decline_task(self.task_id, reason=self.reason.value)

        # Notify user in their channel
        task = await get_task(self.task_id)
        if task:
            lang = get_user_language(task["user_name"])
            channel = discord.utils.get(
                bot.get_all_channels(),
                name=f"{task['workspace']}-commands"
            )
            if channel:
                await channel.send(
                    embed=embed_declined(
                        task_id=self.task_id,
                        reason=self.reason.value,
                        lang=lang
                    )
                )

        # Disable buttons
        for item in self.approval_view.children:
            item.disabled = True
        await interaction.message.edit(view=self.approval_view)

        await interaction.response.send_message(
            embed=embed_info(
                f"Task `{self.task_id[:8]}` declined. User notified."
            ),
            ephemeral=True
        )


# ── Embed helpers ────────────────────────────────────────────

def queued_embed(title, description, task_id, lang):
    embed = discord.Embed(
        title=title,
        description=description,
        color=0x3498db
    )
    embed.set_footer(
        text=r("queued_footer", lang).format(task_id=task_id[:8])
    )
    return embed


def success_embed(message):
    return discord.Embed(
        description=f"✅ {message}",
        color=0x2ecc71
    )


def info_embed(message):
    return discord.Embed(
        description=message,
        color=0x95a5a6
    )


def declined_embed(task_id, reason, lang):
    embed = discord.Embed(
        title=r("declined_title", lang),
        description=r("declined_body", lang).format(reason=reason),
        color=0xe74c3c
    )
    embed.set_footer(
        text=r("declined_footer", lang)
    )
    return embed



@bot.tree.command(
    name="setup",
    description="[Operator] Set up all PrismaOS channels and roles"
)
async def setup(interaction: discord.Interaction):

    if not is_operator(interaction.user.name):
        await interaction.response.send_message(
            "This command is for Daniel only.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    role_names = [
        "operator", "candles", "cars",
        "nursing", "food-brand", "property"
    ]

    roles = {}
    for name in role_names:
        existing = discord.utils.get(guild.roles, name=name)
        if existing:
            roles[name] = existing
        else:
            role = await guild.create_role(name=name)
            roles[name] = role

    structure = {
        "OPERATOR": {
            "roles": ["operator"],
            "channels": [
                "operator-log",
                "operator-analytics",
                "operator-errors",
                "operator-system",
            ]
        },
        "CANDLES": {
            "roles": ["candles", "operator"],
            "channels": [
                "candles-commands",
                "candles-content",
                "candles-orders",
                "candles-stock",
                "candles-messages",
                "candles-finance",
            ]
        },
        "NURSING & MASSAGE": {
            "roles": ["nursing", "operator"],
            "channels": [
                "nursing-commands",
                "nursing-content",
                "nursing-bookings",
                "nursing-messages",
                "nursing-finance",
            ]
        },
        "CARS": {
            "roles": ["cars", "operator"],
            "channels": [
                "cars-commands",
                "cars-auction-alerts",
                "cars-inventory",
                "cars-finance",
                "cars-documents",
            ]
        },
        "PROPERTY": {
            "roles": ["property", "operator"],
            "channels": [
                "property-commands",
                "property-deals",
                "property-research",
                "property-finance",
                "property-legal",
            ]
        },
        "FOOD BRAND": {
            "roles": ["food-brand", "operator"],
            "channels": [
                "food-commands",
                "food-content",
                "food-analytics",
                "food-ideas",
            ]
        },
    }

    everyone = guild.default_role

    for category_name, config in structure.items():
        overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=False)
        }
        for role_name in config["roles"]:
            overwrites[roles[role_name]] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        existing_cat = discord.utils.get(
            guild.categories, name=category_name
        )
        if existing_cat:
            category = existing_cat
        else:
            category = await guild.create_category(
                name=category_name,
                overwrites=overwrites
            )

        for channel_name in config["channels"]:
            existing_ch = discord.utils.get(
                guild.text_channels, name=channel_name
            )
            if not existing_ch:
                await guild.create_text_channel(
                    name=channel_name,
                    category=category
                )

    await interaction.followup.send(
        "PrismaOS server setup complete. All channels and roles created.",
        ephemeral=True
    )


# ── Entry point ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not set in .env")
        sys.exit(1)
    bot.run(token)