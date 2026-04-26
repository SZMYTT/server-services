# bot/permissions.py
# Workspace isolation, language detection, operator checks

# ── Channel → workspace mapping ──────────────────────────────

CHANNEL_WORKSPACE_MAP = {
    # Candles
    "candles-commands":       "candles",
    "candles-summary":        "candles",
    "candles-stock-alerts":   "candles",
    "candles-messages":       "candles",
    "candles-marketing":      "candles",

    # Nursing / Massage
    "nursing-commands":       "nursing_massage",
    "nursing-summary":        "nursing_massage",
    "nursing-bookings":       "nursing_massage",
    "nursing-messages":       "nursing_massage",

    # Cars
    "cars-commands":          "cars",
    "cars-summary":           "cars",
    "cars-auction-alerts":    "cars",
    "cars-inventory":         "cars",

    # Property
    "property-commands":      "property",
    "property-research":      "property",
    "property-finance":       "property",
    "property-compliance":    "property",

    # Food brand
    "food-commands":          "food_brand",
    "food-summary":           "food_brand",
    "food-content":           "food_brand",
    "food-analytics":         "food_brand",
}

# ── User → language mapping ───────────────────────────────────
# Discord usernames → language code
# Update with actual Discord usernames once known

USER_LANGUAGE_MAP = {
    "daniel":         "en",
    "alice":          "en",
    "eddie":          "en",
    "eddies_brother": "lt",   # Lithuanian
    "asta":           "lt",   # Lithuanian
    "alicja":         "en",
}

DEFAULT_LANGUAGE = "en"

# ── Operator list ────────────────────────────────────────────

OPERATORS = ["szmyt_"]

# ── Functions ────────────────────────────────────────────────

def get_workspace_from_channel(channel_name: str) -> str | None:
    """Return workspace key for a given channel name, or None."""
    return CHANNEL_WORKSPACE_MAP.get(channel_name)


def get_user_language(discord_username: str) -> str:
    """Return language code for a user. Defaults to English."""
    # Normalise username — lowercase, strip discriminator if present
    username = discord_username.lower().split("#")[0]
    return USER_LANGUAGE_MAP.get(username, DEFAULT_LANGUAGE)


def is_operator(discord_username: str) -> bool:
    """Return True if user is a system operator (Daniel)."""
    username = discord_username.lower().split("#")[0]
    return username in OPERATORS


def user_can_access_workspace(
    discord_username: str,
    workspace: str
) -> bool:
    """
    Currently all users can run commands in any channel they
    have access to — Discord channel permissions handle isolation.
    This function exists for future tighter access control.
    """
    return True
