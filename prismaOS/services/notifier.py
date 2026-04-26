import asyncio
import logging
import discord
from discord.ext import tasks
import psycopg2
import psycopg2.extras
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.queue import add_task, _get_conn

logger = logging.getLogger("prisma.notifier")

class DraftReplyView(discord.ui.View):
    def __init__(self, task_id: str, workspace: str, user: str, original_input: str):
        super().__init__(timeout=None)
        self.task_id = task_id
        self.workspace = workspace
        self.user = user
        self.original_input = original_input

    @discord.ui.button(label="Send", style=discord.ButtonStyle.success, emoji="📩", custom_id="draft_reply_send")
    async def send_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await add_task(
            workspace=self.workspace,
            user=interaction.user.name,
            task_type="action",
            risk_level="financial", 
            module="customer_comms",
            input=f"Send generated reply from task {self.task_id}",
            trigger_type="discord"
        )
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(f"Initiating physical send for task {self.task_id[:8]}...", ephemeral=True)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="draft_reply_edit")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Open edit modal
        await interaction.response.send_modal(EditDraftModal(self.task_id, self.workspace, self))

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="✗", custom_id="draft_reply_decline")
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Discarded draft.", ephemeral=True)


class EditDraftModal(discord.ui.Modal, title="Edit Reply Draft"):
    new_draft = discord.ui.TextInput(
        label="Your modified draft",
        style=discord.TextStyle.paragraph,
        placeholder="Type the updated reply here...",
        min_length=1,
        max_length=2000
    )

    def __init__(self, task_id: str, workspace: str, view: discord.ui.View):
        super().__init__()
        self.task_id = task_id
        self.workspace = workspace
        self.view_context = view

    async def on_submit(self, interaction: discord.Interaction):
        await add_task(
            workspace=self.workspace,
            user=interaction.user.name,
            task_type="action",
            risk_level="financial", 
            module="customer_comms",
            input=f"Send custom reply for task {self.task_id}:\n{self.new_draft.value}",
            trigger_type="discord"
        )
        for item in self.view_context.children:
            item.disabled = True
        await interaction.message.edit(view=self.view_context)
        await interaction.response.send_message(f"Initiating physical send with edited draft for {self.task_id[:8]}...", ephemeral=True)


def _fetch_unnotified():
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, workspace, user_name, task_type, module, status, input, output
                FROM tasks 
                WHERE status IN ('done', 'failed') AND notified_at IS NULL
                ORDER BY completed_at ASC LIMIT 10
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[NOTIFIER] Failed to fetch unnotified tasks: {e}")
        return []
    finally:
        conn.close()


def _mark_notified(task_id: str):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE tasks SET notified_at = NOW() WHERE id = %s", (task_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"[NOTIFIER] Error marking notified: {e}")
    finally:
        conn.close()


async def post_result(bot: discord.Client, task: dict):
    """Post task result back to relevant discord channel."""
    workspace = task["workspace"]
    
    channel_name = f"{workspace}-summary"
    if task["task_type"] == "comms":
        channel_name = f"{workspace}-messages"
        
    channel = discord.utils.get(bot.get_all_channels(), name=channel_name)
    if not channel:
        logger.warning(f"[NOTIFIER] Channel {channel_name} not found, dropping notification for {task['id'][:8]}")
        return
        
    color = 0x2ecc71 if task["status"] == "done" else 0xe74c3c
    title = f"Task {task['status'].capitalize()}: {task['task_type'].capitalize()}"
    
    output_text = task.get("output") or "No output."
    if len(output_text) > 2000:
        output_text = output_text[:1997] + "..."
        
    embed = discord.Embed(
        title=title,
        description=output_text,
        color=color
    )
    embed.set_footer(text=f"Task ID: {task['id'][:8]} | Module: {task['module']}")

    if task["task_type"] == "comms" and task["status"] == "done":
        view = DraftReplyView(
            task_id=task["id"],
            workspace=workspace,
            user=task["user_name"],
            original_input=task.get("input", "")
        )
        await channel.send(embed=embed, view=view)
    else:
        await channel.send(embed=embed)


async def notify_loop(bot: discord.Client):
    """
    Background loop that runs on the same event loop as the Discord bot.
    Polls the database for Tasks that are done but not notified to the user yet.
    """
    logger.info("[NOTIFIER] Started notification polling loop")
    while not bot.is_closed():
        try:
            loop = asyncio.get_running_loop()
            tasks_to_notify = await loop.run_in_executor(None, _fetch_unnotified)
            
            for t in tasks_to_notify:
                await post_result(bot, t)
                await loop.run_in_executor(None, _mark_notified, t["id"])
            
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"[NOTIFIER] Error in loop: {e}")
            await asyncio.sleep(10)
