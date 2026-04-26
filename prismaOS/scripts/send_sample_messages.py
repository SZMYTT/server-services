#!/usr/bin/env python3
"""
scripts/send_sample_messages.py
Sends sample Discord messages to all workspace channels
so you can preview and edit the embed formatting.

Run from project root:
    ./venv/bin/python3 scripts/send_sample_messages.py
"""

import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord
from dotenv import load_dotenv
from bot.embeds import (
    embed_queued, embed_running, embed_done,
    embed_failed_final, embed_declined,
    embed_operator_alert, embed_success, embed_info
)

load_dotenv()

SAMPLES = [
    # (channel_name, embed, label)

    # ── OPERATOR ─────────────────────────────────────────────
    ("operator-log", lambda: embed_operator_alert(
        task_id="abc12345-xxxx",
        workspace="candles",
        user="alice",
        task_type="content",
        risk_level="public",
        input_text="Write me 3 Instagram posts for our new lavender candle range launching next week."
    ), "Operator alert — public risk task"),

    ("operator-log", lambda: embed_operator_alert(
        task_id="def67890-xxxx",
        workspace="cars",
        user="eddie",
        task_type="comms",
        risk_level="financial",
        input_text="Reply to Mark who says the gearbox is slipping after 2 weeks."
    ), "Operator alert — financial risk task"),

    ("operator-analytics", lambda: embed_info(
        "📊 **Weekly Digest — w/c 14 Apr 2026**\n\n"
        "**Tasks completed:** 14\n"
        "**Approved:** 11 · **Declined:** 2 · **Failed:** 1\n\n"
        "**Busiest workspace:** candles (6 tasks)\n"
        "**Slowest module:** research (avg 4.2 min)\n"
        "**Most used model:** llama3.3:70b"
    ), "Weekly analytics digest"),

    ("operator-errors", lambda: embed_failed_final(
        task_id="err99999",
        error="Ollama connection refused at http://macbook-pro:11434/api/generate — connection timed out after 3 retries"
    ), "Permanent task failure alert"),

    # ── CANDLES ──────────────────────────────────────────────
    ("candles-commands", lambda: embed_queued(
        title="Research task queued",
        description="Got it! I've queued a research task for:\n\n**Best Etsy SEO strategies for candle shops in 2026**\n\nDaniel will review shortly.",
        task_id="aaa11111",
        lang="en",
        risk_level="internal"
    ), "Queued — internal task"),

    ("candles-content", lambda: embed_done(
        task_id="bbb22222",
        task_type="content",
        module="content",
        duration_ms=48200,
        output=(
            "**Post 1 — Launch day** 🕯️✨\n"
            "Introducing our new Lavender & Oat hand-poured candle. "
            "Made in small batches, every pour is crafted with love. "
            "The perfect self-care treat or gift. 🌿\n\n"
            "**Post 2 — Weekend mood** 🌙\n"
            "Dim the lights. Light your candle. You deserve this moment. "
            "Our new Lavender & Oat fills the room with the softest calm. "
            "Grab yours before they sell out. ✨\n\n"
            "#HandPoured #SmallBusiness #CandleLover #EtsyUK"
        )
    ), "Content done — draft inline"),

    ("candles-orders", lambda: embed_info(
        "📦 **New Etsy order** · #OR-1042\n\n"
        "**Customer:** Sarah M.\n"
        "**Items:** Lavender & Oat (x2), Vanilla Spice (x1)\n"
        "**Address:** 14 Rose Hill, Leeds, LS6 2BG\n\n"
        "Use `/order shipped OR-1042` once packed to generate a Royal Mail label."
    ), "New order notification"),

    ("candles-stock", lambda: embed_info(
        "⚠️ **Low stock alert**\n\n"
        "**Vanilla Spice** — 2 units remaining (threshold: 5)\n"
        "**Lavender & Oat** — 1 unit remaining (threshold: 5)\n\n"
        "Use `/stock add [item] [qty]` to restock."
    ), "Low stock alert"),

    ("candles-messages", lambda: embed_done(
        task_id="ccc33333",
        task_type="comms",
        module="customer_comms",
        duration_ms=12400,
        output=(
            "**Message from:** Claire H. (Etsy)\n"
            "**Their message:** Hi! I ordered 2 weeks ago and the tracking hasn't updated. Very worried.\n\n"
            "---\n"
            "**✍️ Suggested reply:**\n"
            "Hi Claire! I'm so sorry to hear that — let me look into this right away for you. "
            "Royal Mail tracking can sometimes lag behind. I'll chase this today and get back to "
            "you by end of day. Thank you so much for your patience! 🌿"
        )
    ), "Customer message draft"),

    ("candles-finance", lambda: embed_done(
        task_id="ddd44444",
        task_type="finance",
        module="finance",
        duration_ms=31000,
        output=(
            "**Weekly Sales Summary — w/c 14 Apr**\n\n"
            "**Revenue:** £84.50\n"
            "**Orders:** 6\n"
            "**Best seller:** Lavender & Oat (4 units)\n"
            "**Avg order value:** £14.08\n\n"
            "**Materials cost est.:** £22.00\n"
            "**Gross margin est.:** ~74%"
        )
    ), "Finance summary"),

    # ── NURSING & MASSAGE ─────────────────────────────────────
    ("nursing-commands", lambda: embed_queued(
        title="Content task queued",
        description="Nice! Queued a Facebook post for:\n\n**Benefits of electric glove therapy for joint pain**\n\nDaniel will review before publishing.",
        task_id="eee55555",
        lang="lt",
        risk_level="public"
    ), "Content queued — public risk"),

    ("nursing-content", lambda: embed_done(
        task_id="fff66666",
        task_type="content",
        module="content",
        duration_ms=28900,
        output=(
            "🌿 **Did you know?**\n\n"
            "Electric glove therapy has been shown to significantly reduce joint stiffness and improve "
            "circulation in hands and wrists — particularly beneficial for arthritis sufferers.\n\n"
            "At our clinic we offer gentle, professional treatments delivered with full medical supervision.\n\n"
            "📅 Consultations available Tuesday–Saturday. DM us to book your slot."
        )
    ), "Nursing Facebook post draft"),

    ("nursing-bookings", lambda: embed_info(
        "📅 **New booking request**\n\n"
        "**Patient:** Rasa K.\n"
        "**Requested:** Electric Glove Treatment\n"
        "**Preferred time:** Saturday morning\n\n"
        "Respond to confirm or suggest an alternative time."
    ), "New booking"),

    ("nursing-messages", lambda: embed_done(
        task_id="ggg77777",
        task_type="comms",
        module="customer_comms",
        duration_ms=9800,
        output=(
            "**Message from:** Rasa K. (Facebook)\n"
            "**Their message:** Hello, I have severe pain in my hands from rheumatoid arthritis. Will this help?\n\n"
            "---\n"
            "**✍️ Suggested reply:**\n"
            "Hello Rasa, thank you for reaching out. Electric glove therapy can provide meaningful relief "
            "for many patients with joint conditions. I'd recommend we start with a free 15-minute "
            "consultation to assess your specific case before any treatment. "
            "Would this week work for you? 🌿"
        )
    ), "Patient message draft"),

    # ── CARS ──────────────────────────────────────────────────
    ("cars-commands", lambda: embed_queued(
        title="Auction scan queued",
        description="Scanning today's BCA and Manheim listings for target criteria. Back shortly!",
        task_id="hhh88888",
        lang="en",
        risk_level="internal"
    ), "Auction scan queued"),

    ("cars-auction-alerts", lambda: embed_done(
        task_id="iii99999",
        task_type="auction",
        module="auction_sourcing",
        duration_ms=87000,
        output=(
            "**Today's Auction Scan — BCA Birmingham**\n\n"
            "**3 potential targets identified:**\n\n"
            "🚗 **2019 Ford Focus 1.0 EcoBoost** — Est. £4,200\n"
            "HPI: Clear · MOT: 8 months · Mileage: 62k · No advisories\n\n"
            "🚗 **2018 Vauxhall Astra 1.4T** — Est. £3,800\n"
            "HPI: Clear · MOT: 4 months · Mileage: 71k · Minor advisory: rear wiper\n\n"
            "🚗 **2020 Nissan Micra 1.0 IG-T** — Est. £5,100\n"
            "HPI: Clear · MOT: 11 months · Mileage: 38k · No advisories\n\n"
            "Recommended: Focus & Micra. Avoid Astra (short MOT reduces quick-flip margin)."
        )
    ), "Auction scan results"),

    ("cars-inventory", lambda: embed_info(
        "🚗 **Current Stock — 3 vehicles**\n\n"
        "**1.** 2019 Ford Focus ST — Reg: AB19 XYZ · Status: **For Sale** · Listed: £7,500\n"
        "**2.** 2017 Honda Civic — Reg: GH17 DEF · Status: **In Prep** · MOT booked: 25 Apr\n"
        "**3.** 2021 Toyota Yaris — Reg: LM21 PQR · Status: **Sold** · Awaiting collection"
    ), "Vehicle inventory"),

    ("cars-documents", lambda: embed_done(
        task_id="jjj00000",
        task_type="document",
        module="document_analyser",
        duration_ms=22400,
        output=(
            "**MOT History Analysis — AB19 XYZ (2019 Ford Focus)**\n\n"
            "**Tests:** 3 · **Passes:** 3 · **Failures:** 0\n\n"
            "**Advisories pattern:**\n"
            "2023: Tyre wear (nearside rear) — typical for age\n"
            "2024: None\n\n"
            "**Assessment:** Clean history. No recurring faults. Safe to sell."
        )
    ), "MOT document analysis"),

    # ── PROPERTY ─────────────────────────────────────────────
    ("property-commands", lambda: embed_queued(
        title="Research task queued",
        description="Queued a property research task for:\n\n**Best buy-to-let yields in Nottingham under £150k**\n\nDaniel will review shortly.",
        task_id="kkk11111",
        lang="en",
        risk_level="internal"
    ), "Property research queued"),

    ("property-deals", lambda: embed_done(
        task_id="lll22222",
        task_type="research",
        module="research",
        duration_ms=193000,
        output=(
            "**Deal Analysis — 42 Morton Street, Nottingham NG7 3BT**\n\n"
            "**Asking price:** £118,000\n"
            "**Estimated rental income:** £750/month\n"
            "**Gross yield:** 7.63% ✅\n"
            "**Stamp duty (SDLT):** £4,150\n"
            "**Estimated refurb:** £12,000–£18,000 (kitchen + bathroom)\n"
            "**Breakeven:** ~22 months\n\n"
            "**Verdict:** Strong yield. Check EPC rating (C minimum for rental compliance)."
        )
    ), "Property deal analysis"),

    ("property-legal", lambda: embed_done(
        task_id="mmm33333",
        task_type="legal",
        module="legal_compliance",
        duration_ms=41200,
        output=(
            "**Rental Compliance Checklist — 42 Morton Street**\n\n"
            "✅ EPC required — minimum rating E (target C by 2028)\n"
            "✅ EICR (Electrical) — valid for 5 years, obtain before tenants\n"
            "✅ Gas Safety Certificate — annual renewal required\n"
            "⚠️ Selective licensing — check if NG7 postcode is in scheme\n"
            "✅ Deposit — register with TDS/DPS within 30 days of receipt"
        )
    ), "Legal compliance check"),

    # ── FOOD BRAND ────────────────────────────────────────────
    ("food-commands", lambda: embed_queued(
        title="Content task queued",
        description="Queued a TikTok content brief for:\n\n**High protein breakfast ideas under 500 calories**\n\nDaniel will review before publishing.",
        task_id="nnn44444",
        lang="en",
        risk_level="public"
    ), "Food content queued"),

    ("food-content", lambda: embed_done(
        task_id="ooo55555",
        task_type="content",
        module="content",
        duration_ms=34700,
        output=(
            "**TikTok Script — High Protein Breakfast Ideas**\n\n"
            "🎬 **HOOK (0–3s):** *'You're skipping the most important macro every morning — here's how to fix it in 5 minutes ⚡'*\n\n"
            "**MAIN (3–30s):**\n"
            "Option 1: Greek yoghurt + berries + 1 scoop protein — 42g protein\n"
            "Option 2: 4 eggs scrambled + spinach + feta — 38g protein\n"
            "Option 3: Overnight oats + almond butter + banana — 31g protein\n\n"
            "**CTA (30–35s):** *'Follow for more clean eating hacks. Save this for Monday morning 💪'*\n\n"
            "**Hashtags:** #HighProtein #CleanEating #MealPrep #FitnessTips #HealthyBreakfast"
        )
    ), "Food TikTok script done"),

    ("food-analytics", lambda: embed_done(
        task_id="ppp66666",
        task_type="research",
        module="analytics",
        duration_ms=56000,
        output=(
            "**Weekly Analytics — @foodbrand Instagram**\n\n"
            "**Followers:** 847 (+23 this week)\n"
            "**Best post:** Overnight oats reel — 1,204 views, 89 likes\n"
            "**Avg engagement rate:** 4.2%\n"
            "**Story views:** 312\n"
            "**Profile visits:** 156\n\n"
            "**Recommendation:** Post reels at 7pm–9pm Tue/Thu for best reach."
        )
    ), "Food analytics summary"),

    ("food-ideas", lambda: embed_info(
        "💡 **10 Content Ideas Generated for This Week**\n\n"
        "1. 'What I eat in a day — 2,000 calories, high protein'\n"
        "2. Protein pancake recipe (3 ingredients)\n"
        "3. Debunking: 'Carbs make you fat' myth\n"
        "4. Meal prep Sunday — 5 meals in 45 minutes\n"
        "5. Rating viral TikTok 'health' foods\n"
        "6. My grocery haul for £40\n"
        "7. Protein coffee — does it work?\n"
        "8. Before/after: 6 weeks of consistent eating\n"
        "9. The one supplement I actually recommend\n"
        "10. Q&A: your most asked nutrition questions"
    ), "AI content ideas"),
]


async def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not in .env")
        return

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Connected as {client.user}. Sending sample messages...")
        sent = 0
        skipped = 0
        for channel_name, embed_fn, label in SAMPLES:
            channel = discord.utils.get(client.get_all_channels(), name=channel_name)
            if not channel:
                print(f"  SKIP  #{channel_name} not found (run /setup first)")
                skipped += 1
                continue
            try:
                await channel.send(
                    content=f"*Sample: {label}*",
                    embed=embed_fn()
                )
                print(f"  SENT  #{channel_name} — {label}")
                sent += 1
                await asyncio.sleep(0.8)  # avoid Discord rate limits
            except Exception as e:
                print(f"  FAIL  #{channel_name}: {e}")

        print(f"\nDone. Sent: {sent} | Skipped (channel missing): {skipped}")
        await client.close()

    await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
