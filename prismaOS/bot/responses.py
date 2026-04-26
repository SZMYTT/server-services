# bot/responses.py
# Localised response strings for Prisma bot.
# Languages: English (en), Lithuanian (lt)
#
# Prisma's personality: friendly and warm, like a helpful colleague.
# Not corporate, not robotic. Approachable and clear.

STRINGS = {

    # ── General ──────────────────────────────────────────────

    "wrong_channel": {
        "en": "Hey! That command isn't available in this channel. "
              "Head to your workspace commands channel and try again 😊",
        "lt": "Labas! Ši komanda šiame kanale neveikia. "
              "Pabandyk savo darbo srities komandų kanale 😊"
    },

    "not_available": {
        "en": "That command isn't available for this workspace.",
        "lt": "Ši komanda šiai darbo sričiai negalima."
    },

    "no_tasks": {
        "en": "You don't have any active tasks right now.",
        "lt": "Šiuo metu neturite aktyvių užduočių."
    },

    # ── Queue confirmations ───────────────────────────────────

    "queued_footer": {
        "en": "Task ID: {task_id} · Waiting for approval",
        "lt": "Užduoties ID: {task_id} · Laukiama patvirtinimo"
    },

    # Research
    "research_queued_title": {
        "en": "🔍 Research task queued",
        "lt": "🔍 Tyrimo užduotis pateikta"
    },
    "research_queued_body": {
        "en": "Got it! I've queued a research task for:\n\n"
              "**{topic}**\n\n"
              "Daniel will review it shortly and I'll post the "
              "results here when it's done.",
        "lt": "Supratau! Pateikiau tyrimo užduotį:\n\n"
              "**{topic}**\n\n"
              "Danielius netrukus peržiūrės ir rezultatus "
              "paskelbsiu čia."
    },

    # Summary
    "summary_queued_title": {
        "en": "📊 Summary queued",
        "lt": "📊 Suvestinė pateikta"
    },
    "summary_queued_body": {
        "en": "On it! Generating your **{period}** summary. "
              "I'll post it here shortly.",
        "lt": "Gerai! Ruošiu jūsų **{period}** suvestinę. "
              "Netrukus paskelbsiu čia."
    },

    # Content
    "content_queued_title": {
        "en": "✍️ Content task queued",
        "lt": "✍️ Turinio užduotis pateikta"
    },
    "content_queued_body": {
        "en": "Nice! I've queued a content task for:\n\n"
              "**{brief}**\n\n"
              "Daniel will review it and once approved I'll get "
              "to work. Results posted here when ready.",
        "lt": "Puiku! Pateikiau turinio užduotį:\n\n"
              "**{brief}**\n\n"
              "Danielius peržiūrės ir patvirtinus pradėsiu dirbti."
    },

    # Messages
    "messages_queued_title": {
        "en": "💬 Fetching your messages",
        "lt": "💬 Gaunami jūsų pranešimai"
    },
    "messages_queued_body": {
        "en": "I'll pull your pending messages and draft some "
              "replies for you. Daniel will review before anything "
              "gets sent.",
        "lt": "Gausiu jūsų laukiančius pranešimus ir paruošiu "
              "atsakymų juodraščius. Danielius peržiūrės prieš "
              "išsiunčiant."
    },

    # Stock
    "stock_queued_title": {
        "en": "📦 Checking stock levels",
        "lt": "📦 Tikrinami atsargų lygiai"
    },
    "stock_queued_body": {
        "en": "Checking your current stock levels now. "
              "Results coming up shortly!",
        "lt": "Dabar tikrinu jūsų atsargų lygius. "
              "Rezultatai netrukus!"
    },

    # Auctions
    "auctions_queued_title": {
        "en": "🚗 Fetching auction listings",
        "lt": "🚗 Gaunami aukcionų skelbimai"
    },
    "auctions_queued_body": {
        "en": "Scanning today's auction listings for good "
              "opportunities. Back shortly!",
        "lt": "Peržiūriu šiandienos aukcionų skelbimus. "
              "Grįšiu netrukus!"
    },

    # ── Decline messages ──────────────────────────────────────

    "declined_title": {
        "en": "Task not approved",
        "lt": "Užduotis nepatvirtinta"
    },
    "declined_body": {
        "en": "Hey, Daniel reviewed your request and decided "
              "not to run it this time.\n\n"
              "**Reason:** {reason}\n\n"
              "Feel free to adjust your request and try again!",
        "lt": "Labas, Danielius peržiūrėjo jūsų užklausą ir "
              "nusprendė jos nevykdyti.\n\n"
              "**Priežastis:** {reason}\n\n"
              "Galite pakoreguoti užklausą ir bandyti dar kartą!"
    },
    "declined_footer": {
        "en": "Questions? Message Daniel directly.",
        "lt": "Klausimai? Parašykite Danieliui tiesiogiai."
    },

    # ── Result delivery ───────────────────────────────────────

    "result_title": {
        "en": "✅ Task complete",
        "lt": "✅ Užduotis atlikta"
    },
    "result_summary_label": {
        "en": "Summary",
        "lt": "Santrauka"
    },
    "result_full_report": {
        "en": "📄 Full report available in the web dashboard",
        "lt": "📄 Pilna ataskaita prieinama žiniatinklio valdymo skydelyje"
    },
    "result_footer": {
        "en": "Task ID: {task_id} · Module: {module}",
        "lt": "Užduoties ID: {task_id} · Modulis: {module}"
    },

    # ── Scheduled task results ────────────────────────────────

    "scheduled_result_title": {
        "en": "📅 Scheduled report ready",
        "lt": "📅 Suplanuota ataskaita paruošta"
    },

}


def r(key: str, lang: str = "en") -> str:
    """
    Get a localised response string.
    Falls back to English if key not found in requested language.
    Falls back to key name if not found at all.
    """
    entry = STRINGS.get(key, {})
    return entry.get(lang) or entry.get("en") or key
