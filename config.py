import os

from dotenv import load_dotenv


# ============================================================
# BUSINESS NEWS AGENT - CONFIGURATION
# ============================================================

# Load variables from the root .env file
load_dotenv()


# ============================================================
# API KEYS
# ============================================================

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
)

NEWS_API_KEY = os.getenv(
    "NEWS_API_KEY",
    ""
)


# ============================================================
# API URLS
# ============================================================

NEWSAPI_URL = (
    "https://newsapi.org/v2/everything"
)


# ============================================================
# DATABASE
# ============================================================

DATABASE_NAME = os.getenv(
    "DATABASE_NAME",
    "news.db"
)


# ============================================================
# CONFIGURATION VALIDATION
# ============================================================

def validate_config():

    missing = []

    if not GEMINI_API_KEY:

        missing.append(
            "GEMINI_API_KEY"
        )

    if not NEWS_API_KEY:

        missing.append(
            "NEWS_API_KEY"
        )

    if missing:

        print()
        print("=" * 70)
        print("CONFIGURATION STATUS")
        print("=" * 70)

        print(
            "The following configuration values "
            "are currently missing:"
        )

        for variable in missing:

            print(
                f" - {variable}"
            )

        print("=" * 70)

        return False

    return True


# ============================================================
# SAFE STATUS CHECK
# ============================================================

def show_config_status():

    print()
    print("=" * 70)
    print("BUSINESS NEWS AGENT - CONFIG STATUS")
    print("=" * 70)

    print(
        "GEMINI_API_KEY loaded:",
        bool(GEMINI_API_KEY)
    )

    print(
        "NEWS_API_KEY loaded:",
        bool(NEWS_API_KEY)
    )

    print(
        "DATABASE_NAME:",
        DATABASE_NAME
    )

    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    show_config_status()
    