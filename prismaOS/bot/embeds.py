# bot/embeds.py
# PrismaOS rich Discord embed factory.
#
# Every distinct bot event has its own embed format with clear
# visual hierarchy, colour coding, and contextual information.
# Never use generic embeds where a specific one exists.
#
# Colour standards (per AGENTS.md):
#   0x95a5a6  Grey    — informational / internal
#   0x3498db  Blue    — running / in progress
#   0x2ecc71  Green   — success / done
#   0xf39c12  Amber   — public risk / warning / retry
#   0xe74c3c  Red     — financial risk / declined / error

import discord
from bot.responses import r

# ──────────────────────────────────────────────────────────────
# QUEUED embeds  (one per risk tier)
# ──────────────────────────────────────────────────────────────

def embed_queued_internal(title: str, body: str, task_id: str, lang: str = "en") -> discord.Embed:
    """Grey — internal task, auto-approved flow."""
    embed = discord.Embed(title=f"🕐 {title}", description=body, color=0x95a5a6)
    embed.set_footer(text=r("queued_footer", lang).format(task_id=task_id[:8]))
    return embed


def embed_queued_public(title: str, body: str, task_id: str, lang: str = "en") -> discord.Embed:
    """Amber — public-risk task, needs Daniel approval before running."""
    embed = discord.Embed(title=f"⚠️ {title}", description=body, color=0xf39c12)
    embed.add_field(
        name="Approval required",
        value="Daniel will review this before it runs. You'll be notified here.",
        inline=False,
    )
    embed.set_footer(text=r("queued_footer", lang).format(task_id=task_id[:8]))
    return embed


def embed_queued_financial(title: str, body: str, task_id: str, lang: str = "en") -> discord.Embed:
    """Red — financial/high-risk task, immediate operator alert."""
    embed = discord.Embed(title=f"🔐 {title}", description=body, color=0xe74c3c)
    embed.add_field(
        name="⚠️ High-risk action",
        value="This has real-world consequences. Daniel must approve before anything is sent.",
        inline=False,
    )
    embed.set_footer(text=r("queued_footer", lang).format(task_id=task_id[:8]))
    return embed


def embed_queued(
    title: str,
    description: str,
    task_id: str,
    lang: str = "en",
    risk_level: str = "internal",
) -> discord.Embed:
    """Main entry point — picks the right queued embed by risk level."""
    if risk_level == "financial":
        return embed_queued_financial(title, description, task_id, lang)
    elif risk_level == "public":
        return embed_queued_public(title, description, task_id, lang)
    else:
        return embed_queued_internal(title, description, task_id, lang)


# ──────────────────────────────────────────────────────────────
# RUNNING embed
# ──────────────────────────────────────────────────────────────

def embed_running(task_id: str, task_type: str, step_name: str = "", lang: str = "en") -> discord.Embed:
    """Blue — task is actively being processed."""
    embed = discord.Embed(
        title=f"⚡ Running — {task_type.replace('_', ' ').title()}",
        color=0x3498db,
    )
    if step_name:
        embed.add_field(name="Current step", value=step_name.replace("_", " ").title(), inline=True)
    embed.set_footer(text=f"Task ID: {task_id[:8]}")
    return embed


# ──────────────────────────────────────────────────────────────
# DONE embeds  (per task type for contextual output)
# ──────────────────────────────────────────────────────────────

def embed_done_research(task_id: str, output: str, module: str, duration_ms: int, lang: str = "en") -> discord.Embed:
    """Green — research task complete. Shows preview + link."""
    preview = output[:300].strip() + ("…" if len(output) > 300 else "")
    embed = discord.Embed(
        title="📄 Research complete",
        description=preview,
        color=0x2ecc71,
    )
    embed.add_field(name="Full report", value="Available in the web dashboard", inline=True)
    embed.set_footer(text=_done_footer(task_id, module, duration_ms))
    return embed


def embed_done_content(task_id: str, output: str, module: str, duration_ms: int, lang: str = "en") -> discord.Embed:
    """Green — content task complete. Shows full draft inline if short enough."""
    embed = discord.Embed(title="✍️ Content draft ready", color=0x2ecc71)
    if len(output) <= 1800:
        embed.description = output
    else:
        embed.description = output[:1800].strip() + "\n\n*… (continued in web dashboard)*"
    embed.set_footer(text=_done_footer(task_id, module, duration_ms))
    return embed


def embed_done_finance(task_id: str, output: str, module: str, duration_ms: int, lang: str = "en") -> discord.Embed:
    """Green — finance/document task complete. Shows key figures."""
    preview = output[:500].strip() + ("…" if len(output) > 500 else "")
    embed = discord.Embed(
        title="💰 Finance report ready",
        description=preview,
        color=0x2ecc71,
    )
    embed.add_field(name="Full report", value="Available in the web dashboard", inline=True)
    embed.set_footer(text=_done_footer(task_id, module, duration_ms))
    return embed


def embed_done_generic(task_id: str, task_type: str, output: str, module: str, duration_ms: int, lang: str = "en") -> discord.Embed:
    """Green — catch-all done embed for generic task types."""
    title_map = {
        "legal":    "⚖️ Legal analysis complete",
        "website":  "🌐 Website content ready",
        "document": "📋 Document analysis complete",
        "auction":  "🚗 Auction scan complete",
        "comms":    "💬 Message drafts ready",
    }
    title = title_map.get(task_type, f"✅ {task_type.replace('_', ' ').title()} complete")
    preview = output[:400].strip() + ("…" if len(output) > 400 else "")
    embed = discord.Embed(title=title, description=preview, color=0x2ecc71)
    embed.add_field(name="Full output", value="Available in the web dashboard", inline=True)
    embed.set_footer(text=_done_footer(task_id, module, duration_ms))
    return embed


def embed_done(
    task_id: str,
    task_type: str,
    output: str,
    module: str,
    duration_ms: int = 0,
    lang: str = "en",
) -> discord.Embed:
    """Main entry point — picks the right done embed by task type."""
    if task_type == "research":
        return embed_done_research(task_id, output, module, duration_ms, lang)
    elif task_type == "content":
        return embed_done_content(task_id, output, module, duration_ms, lang)
    elif task_type in ("finance", "document"):
        return embed_done_finance(task_id, output, module, duration_ms, lang)
    else:
        return embed_done_generic(task_id, task_type, output, module, duration_ms, lang)


# ──────────────────────────────────────────────────────────────
# FAILED embeds
# ──────────────────────────────────────────────────────────────

def embed_failed_retry(task_id: str, retry_count: int, error: str, lang: str = "en") -> discord.Embed:
    """Amber — task failed but is being retried."""
    embed = discord.Embed(
        title=f"🔄 Retrying… (attempt {retry_count})",
        description=f"There was a hiccup, but I'm trying again automatically.\n\n**Error:** `{error[:150]}`",
        color=0xf39c12,
    )
    embed.set_footer(text=f"Task ID: {task_id[:8]}")
    return embed


def embed_failed_final(task_id: str, error: str, lang: str = "en") -> discord.Embed:
    """Dark red — task permanently failed after all retries."""
    embed = discord.Embed(
        title="✗ Task failed",
        description=(
            f"This task couldn't be completed after multiple attempts.\n\n"
            f"**Error:** `{error[:200]}`\n\n"
            "You can try again with a clearer request, or contact Daniel for help."
        ),
        color=0xc0392b,
    )
    embed.set_footer(text=f"Task ID: {task_id[:8]} · Daniel has been notified")
    return embed


# ──────────────────────────────────────────────────────────────
# DECLINED embed
# ──────────────────────────────────────────────────────────────

def embed_declined(task_id: str, reason: str, lang: str = "en") -> discord.Embed:
    """Dark red — task declined by operator."""
    embed = discord.Embed(
        title=f"🚫 {r('declined_title', lang)}",
        description=r("declined_body", lang).format(reason=reason),
        color=0xc0392b,
    )
    embed.set_footer(text=r("declined_footer", lang))
    return embed


# ──────────────────────────────────────────────────────────────
# OPERATOR alert embed
# ──────────────────────────────────────────────────────────────

def embed_operator_alert(
    task_id: str,
    workspace: str,
    user: str,
    task_type: str,
    risk_level: str,
    input_text: str,
) -> discord.Embed:
    """Risk-coloured embed for the operator-log channel."""
    colour_map = {"internal": 0x95a5a6, "public": 0xf39c12, "financial": 0xe74c3c}
    icon_map   = {"internal": "⚪", "public": "🟡", "financial": "🔴"}

    embed = discord.Embed(
        title=f"{icon_map.get(risk_level, '⚪')} New task — {workspace}",
        color=colour_map.get(risk_level, 0x95a5a6),
    )
    embed.add_field(name="From",      value=user,                               inline=True)
    embed.add_field(name="Type",      value=task_type.replace("_", " ").title(), inline=True)
    embed.add_field(name="Risk",      value=risk_level.upper(),                 inline=True)
    embed.add_field(name="Request",   value=input_text[:300],                   inline=False)
    embed.set_footer(text=f"Task ID: {task_id[:8]}")
    return embed


# ──────────────────────────────────────────────────────────────
# Simple utility embeds
# ──────────────────────────────────────────────────────────────

def embed_success(message: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {message}", color=0x2ecc71)


def embed_info(message: str) -> discord.Embed:
    return discord.Embed(description=message, color=0x95a5a6)


def embed_error(message: str) -> discord.Embed:
    return discord.Embed(description=f"✗ {message}", color=0xe74c3c)


# ──────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────

def _done_footer(task_id: str, module: str, duration_ms: int) -> str:
    secs = duration_ms / 1000
    if secs >= 60:
        time_str = f"{secs/60:.1f}m"
    else:
        time_str = f"{secs:.1f}s"
    return f"Task ID: {task_id[:8]} · Module: {module} · Took: {time_str}"
