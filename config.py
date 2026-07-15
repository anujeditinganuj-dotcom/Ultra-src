"""
Save Restricted Content Bot Configuration

Developed by: LastPerson07XRexBots
Telegram: @RexBots_Official X @THEUPDATEDGUYS

Please retain this credit if you use or modify this project.
"""

import os


# ==============================
# Telegram Bot Credentials
# ==============================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8694198519:AAHkfsd2hG584oC92jM-Ee2PJd2snDy49qM")
API_ID = int(os.environ.get("API_ID", "37476811"))
API_HASH = os.environ.get("API_HASH", "7aa60670b871050820086c6267371ee6")


# ==============================
# Admin Configuration
# ==============================

# Add admin user IDs separated by commas in environment variables
ADMINS = [int(admin) for admin in os.environ.get("ADMINS", "8730393744").split(",") if admin]


# ==============================
# Database Configuration
# ==============================

DB_URI = os.environ.get("DB_URI", "mongodb+srv://Anujedit:Anujedit@cluster0.7cs2nhd.mongodb.net/?appName=Cluster0")
DB_NAME = os.environ.get("DB_NAME", "SaveRestricted2")


# ==============================
# Logging Configuration
# ==============================

# Replace with your Telegram log channel ID (example: -1001234567890)
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "-1003824246703"))

# ==============================
# Error Handling
# ==============================

# Set to True to send error messages to users
ERROR_MESSAGE = os.environ.get("ERROR_MESSAGE", "True").lower() == "true"

# ==============================
# Batch Link Limits
# ==============================

# Hard safety caps on how many messages a single batch link can request
MAX_BATCH_IDS_FREE    = int(os.environ.get("MAX_BATCH_IDS_FREE", "50"))
MAX_BATCH_IDS_PREMIUM = int(os.environ.get("MAX_BATCH_IDS_PREMIUM", "200"))

# Selectable options shown in the Settings > Batch Limit menu
BATCH_LIMIT_OPTIONS_FREE    = [10, 25, 50]
BATCH_LIMIT_OPTIONS_PREMIUM = [50, 100, 150, 200]

# ==============================
# YouTube / Instagram Downloader
# ==============================

# Max direct-download file size the bot will upload back to Telegram (bytes)
YTDL_MAX_FILESIZE = int(os.environ.get("YTDL_MAX_FILESIZE", str(2 * 1024 * 1024 * 1024)))  # 2GB
# Batch size for playlist downloads — a playlist of any length is processed
# in chunks of this size, pausing briefly between chunks, rather than being
# truncated. (Historically this was a hard cap; it no longer is.)
YTDL_PLAYLIST_MAX = int(os.environ.get("YTDL_PLAYLIST_MAX", "50"))
# How many total results to fetch for /search (YouTube title/song search).
# These are paginated in the chat (see YTDL_SEARCH_PAGE_SIZE) via Next/Previous buttons.
YTDL_SEARCH_LIMIT = int(os.environ.get("YTDL_SEARCH_LIMIT", "30"))
# How many results to show per page in /search results.
YTDL_SEARCH_PAGE_SIZE = int(os.environ.get("YTDL_SEARCH_PAGE_SIZE", "10"))
YT_COOKIES    = os.environ.get("YT_COOKIES", "youtube/yt_cookies.txt")       # Netscape-format cookies.txt
INSTA_COOKIES = os.environ.get("INSTA_COOKIES", "instagram/insta_cookies.txt")
FB_COOKIES    = os.environ.get("FB_COOKIES", "facebook/fb_cookies.txt")      # Needed for private FB videos

# Username shown in the "Downloaded by @..." line on rich media captions
CREDIT_USERNAME = os.environ.get("CREDIT_USERNAME", "anujedits76")

# ==============================
# Free-Access Token Gate (optional, URL-shortener based)
# ==============================

# Leave WEBSITE_URL / AD_API empty to keep this feature fully disabled.
WEBSITE_URL = os.environ.get("WEBSITE_URL", "")
AD_API      = os.environ.get("AD_API", "")
TOKEN_VALID_HOURS = int(os.environ.get("TOKEN_VALID_HOURS", "3"))
TOKEN_BATCH_BONUS = int(os.environ.get("TOKEN_BATCH_BONUS", "20"))

# ==============================
# Developer Tools (owner-only /eval, /shell)
# ==============================

# Extremely powerful — only ADMINS can ever use these regardless of this flag.
DEV_TOOLS_ENABLED = os.environ.get("DEV_TOOLS_ENABLED", "True").lower() == "true"

# ==============================
# Telegram Stars Payment Plans (/pay)
# ==============================

# label, days, star price — edit freely
STAR_PLANS = {
    "d": {"label": "1 Day",   "days": 1,  "stars": int(os.environ.get("STAR_PRICE_DAY", "15"))},
    "w": {"label": "1 Week",  "days": 7,  "stars": int(os.environ.get("STAR_PRICE_WEEK", "75"))},
    "m": {"label": "1 Month", "days": 30, "stars": int(os.environ.get("STAR_PRICE_MONTH", "250"))},
}

# ==============================
# Bot Mode (Freemium / Paid)
# ==============================

DEFAULT_BOT_MODE = os.environ.get("DEFAULT_BOT_MODE", "paid")  # "paid" or "freemium"

# ==============================
# Referral Program
# ==============================

REFERRAL_REWARD_BUCKS = int(os.environ.get("REFERRAL_REWARD_BUCKS", "50"))   # earned per successful referral
REFERRAL_TRIAL_DAYS   = int(os.environ.get("REFERRAL_TRIAL_DAYS", "1"))      # trial premium given to the new joiner
BUCKS_PER_PREMIUM_DAY = int(os.environ.get("BUCKS_PER_PREMIUM_DAY", "100"))  # redemption rate
