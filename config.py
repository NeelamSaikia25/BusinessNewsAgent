import os


# ============================================================
# BUSINESS NEWS AGENT - CONFIGURATION
# ============================================================


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
# VALIDATION
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
        print("CONFIGURATION ERROR")
        print("=" * 70)

        print(
            "Missing environment variables:"
        )

        for variable in missing:

            print(
                f" - {variable}"
            )

        print()
        print(
            "Set the required environment "
            "variables before running the agent."
        )

        print("=" * 70)

        return False

    return True


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    if validate_config():

        print()
        print(
            "Configuration is valid."
        )