import requests
from datetime import datetime, timedelta

from config import NEWS_API_KEY

from database import (
    create_database,
    repair_database,
    save_news,
    delete_old_news,
    news_exists
)


# ============================================================
# SETTINGS
# ============================================================

NEWS_API_URL = "https://newsapi.org/v2/everything"

FINAL_ARTICLE_COUNT = 5

REQUEST_TIMEOUT = 20

LOOKBACK_HOURS = 48


# ============================================================
# WORD GROUPS
# ============================================================

GLOBAL_ECONOMIC_KEYWORDS = [
    "Federal Reserve",
    "interest rates",
    "central bank",
    "inflation",
    "crude oil",
    "oil prices",
    "natural gas",
    "global economy",
    "global trade",
    "trade war",
    "tariffs",
    "China economy",
    "US economy",
    "European economy",
    "recession",
    "GDP",
    "IMF",
    "World Bank",
    "geopolitics",
    "semiconductor supply chain",
    "chip supply",
    "shipping",
    "supply chain",
    "commodity prices",
    "US dollar",
    "Treasury yields"
]


INDIA_IMPACT_KEYWORDS = [
    "India",
    "Indian economy",
    "RBI",
    "Reserve Bank of India",
    "rupee",
    "INR",
    "Nifty",
    "Sensex",
    "Indian stocks",
    "Indian market",
    "India GDP",
    "India inflation",
    "Indian inflation",
    "Indian exports",
    "Indian imports",
    "India trade",
    "India oil",
    "Indian oil",
    "India energy",
    "Indian energy",
    "India semiconductor",
    "Indian semiconductor",
    "India manufacturing",
    "India investment",
    "foreign investment",
    "FDI",
    "NITI Aayog"
]


IMPORTANT_SOURCES = [
    "Reuters",
    "BBC News",
    "Bloomberg",
    "Financial Times",
    "CNBC",
    "The Wall Street Journal",
    "Associated Press",
    "The Guardian",
    "Al Jazeera",
    "Economic Times",
    "NITI Aayog"
]


# ============================================================
# PROMOTIONAL / IRRELEVANT KEYWORDS
# ============================================================

BLOCKED_KEYWORDS = [
    "deal",
    "deals",
    "coupon",
    "discount",
    "promo",
    "promotion",
    "promotional",
    "sale",
    "shopping",
    "buy now",
    "free shipping",
    "amazon",
    "prime",
    "walmart",
    "best buy",
    "product review",
    "gift guide",
    "holiday shopping",
    "black friday",
    "cyber monday",
    "giveaway",
    "sponsored",
    "affiliate",
    "exclusive offer",
    "limited time offer",
    "price drop",
    "discount code",
    "coupon code"
]


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

create_database()
repair_database()


# ============================================================
# HELPER: NORMALIZE TEXT
# ============================================================

def normalize_text(value):

    if not value:
        return ""

    return str(value).strip().lower()


# ============================================================
# HELPER: CHECK PROMOTIONAL CONTENT
# ============================================================

def is_promotional(article):

    title = normalize_text(article.get("title"))
    description = normalize_text(article.get("description"))
    source = normalize_text(
        article.get("source", {}).get("name")
    )

    combined_text = (
        title
        + " "
        + description
        + " "
        + source
    )

    for word in BLOCKED_KEYWORDS:

        if word in combined_text:

            return True

    return False


# ============================================================
# HELPER: CALCULATE RELEVANCE SCORE
# ============================================================

def calculate_relevance_score(article):

    title = article.get("title") or ""
    description = article.get("description") or ""

    source_name = (
        article.get("source", {}).get("name")
        or ""
    )

    text = normalize_text(
        title
        + " "
        + description
    )

    score = 0

    # --------------------------------------------------------
    # GLOBAL ECONOMIC IMPACT
    # --------------------------------------------------------

    for keyword in GLOBAL_ECONOMIC_KEYWORDS:

        if normalize_text(keyword) in text:

            score += 5


    # --------------------------------------------------------
    # INDIA IMPACT
    # --------------------------------------------------------

    for keyword in INDIA_IMPACT_KEYWORDS:

        if normalize_text(keyword) in text:

            score += 6


    # --------------------------------------------------------
    # IMPORTANT SOURCES
    # --------------------------------------------------------

    for source in IMPORTANT_SOURCES:

        if normalize_text(source) in normalize_text(
            source_name
        ):

            score += 4


    # --------------------------------------------------------
    # TITLE MATCH IS MORE IMPORTANT
    # --------------------------------------------------------

    title_text = normalize_text(title)

    for keyword in (
        GLOBAL_ECONOMIC_KEYWORDS
        + INDIA_IMPACT_KEYWORDS
    ):

        if normalize_text(keyword) in title_text:

            score += 4


    return score


# ============================================================
# HELPER: CHECK WHETHER ARTICLE IS ECONOMIC
# ============================================================

def is_economic_article(article):

    title = normalize_text(
        article.get("title")
    )

    description = normalize_text(
        article.get("description")
    )

    text = title + " " + description

    economic_matches = 0

    for keyword in GLOBAL_ECONOMIC_KEYWORDS:

        if normalize_text(keyword) in text:

            economic_matches += 1


    for keyword in INDIA_IMPACT_KEYWORDS:

        if normalize_text(keyword) in text:

            economic_matches += 1


    return economic_matches >= 1


# ============================================================
# FETCH NEWS FROM NEWSAPI
# ============================================================

def fetch_candidate_news():

    print("\nFetching global economic news...")

    # --------------------------------------------------------
    # Search terms
    #
    # We deliberately use a manageable query instead of
    # constructing a gigantic manually encoded URL.
    # requests will encode the parameters safely.
    # --------------------------------------------------------

    query = (
        '"Federal Reserve" OR '
        '"interest rates" OR '
        '"central bank" OR '
        '"inflation" OR '
        '"crude oil" OR '
        '"oil prices" OR '
        '"global economy" OR '
        '"global trade" OR '
        '"trade war" OR '
        '"tariffs" OR '
        '"China economy" OR '
        '"US economy" OR '
        '"European economy" OR '
        '"recession" OR '
        '"GDP" OR '
        '"IMF" OR '
        '"World Bank" OR '
        '"India economy" OR '
        '"RBI" OR '
        '"Indian economy" OR '
        '"Indian market" OR '
        '"India GDP" OR '
        '"India inflation" OR '
        '"India oil" OR '
        '"India energy" OR '
        '"India trade" OR '
        '"NITI Aayog"'
    )


    # --------------------------------------------------------
    # DATE RANGE
    # --------------------------------------------------------

    from_date = (
        datetime.utcnow()
        - timedelta(hours=LOOKBACK_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%S")


    # --------------------------------------------------------
    # REQUEST PARAMETERS
    # --------------------------------------------------------

    params = {

        "q": query,

        "from": from_date,

        "language": "en",

        "sortBy": "publishedAt",

        "pageSize": 100,

        "page": 1

    }


    headers = {

        "X-Api-Key": NEWS_API_KEY

    }


    try:

        response = requests.get(
            NEWS_API_URL,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )


    except requests.exceptions.ConnectionError as error:

        print("\nNEWSAPI CONNECTION ERROR")

        print(
            "Could not connect to newsapi.org."
        )

        print(
            "This is a network/connection problem, "
            "not a Gemini problem."
        )

        print(
            f"\nDetails: {error}"
        )

        return []


    except requests.exceptions.Timeout:

        print(
            "\nNEWSAPI TIMEOUT:"
        )

        print(
            "NewsAPI did not respond within "
            f"{REQUEST_TIMEOUT} seconds."
        )

        return []


    except requests.exceptions.RequestException as error:

        print(
            f"\nNEWSAPI REQUEST ERROR:\n{error}"
        )

        return []


    # --------------------------------------------------------
    # CHECK HTTP RESPONSE
    # --------------------------------------------------------

    if response.status_code != 200:

        print(
            f"\nNEWSAPI HTTP ERROR: "
            f"{response.status_code}"
        )

        try:

            error_data = response.json()

            print(
                error_data.get(
                    "message",
                    "Unknown NewsAPI error."
                )
            )

        except ValueError:

            print(response.text)

        return []


    # --------------------------------------------------------
    # READ JSON
    # --------------------------------------------------------

    try:

        data = response.json()

    except ValueError:

        print(
            "\nNEWSAPI returned invalid JSON."
        )

        return []


    if data.get("status") != "ok":

        print(
            "\nNEWSAPI ERROR:"
        )

        print(
            data.get(
                "message",
                "Unknown NewsAPI error."
            )
        )

        return []


    articles = data.get(
        "articles",
        []
    )


    print(
        f"\nNewsAPI returned "
        f"{len(articles)} candidate articles."
    )


    return articles


# ============================================================
# FILTER ARTICLES
# ============================================================

def filter_articles(articles):

    print(
        "\nFiltering articles for "
        "global economic + Indian impact..."
    )


    scored_articles = []


    for article in articles:

        # ----------------------------------------------------
        # Ignore malformed articles
        # ----------------------------------------------------

        if not isinstance(article, dict):

            continue


        title = article.get(
            "title"
        )


        url = article.get(
            "url"
        )


        if not title or not url:

            continue


        # ----------------------------------------------------
        # Ignore promotional content
        # ----------------------------------------------------

        if is_promotional(article):

            continue


        # ----------------------------------------------------
        # Ignore articles without economic relevance
        # ----------------------------------------------------

        if not is_economic_article(article):

            continue


        # ----------------------------------------------------
        # Calculate score
        # ----------------------------------------------------

        score = calculate_relevance_score(
            article
        )


        # Require meaningful relevance.
        #
        # This prevents random articles such as:
        # "Orbit 50-Foot Coil Garden Hose..."
        #
        # from entering the final five.
        # ----------------------------------------------------

        if score < 10:

            continue


        scored_articles.append(
            (
                score,
                article
            )
        )


    # --------------------------------------------------------
    # Highest relevance first
    # --------------------------------------------------------

    scored_articles.sort(
        key=lambda item: item[0],
        reverse=True
    )


    # --------------------------------------------------------
    # Remove duplicate URLs
    # --------------------------------------------------------

    selected = []

    seen_urls = set()


    for score, article in scored_articles:

        url = article.get("url")


        if url in seen_urls:

            continue


        seen_urls.add(url)


        selected.append(
            (
                score,
                article
            )
        )


        if len(selected) >= FINAL_ARTICLE_COUNT:

            break


    print(
        f"\nSelected {len(selected)} "
        "high-relevance global/India "
        "economic articles."
    )


    return selected


# ============================================================
# DISPLAY SELECTED ARTICLES
# ============================================================

def display_selected_articles(selected):

    if not selected:

        print(
            "\nNo sufficiently relevant "
            "economic news was found."
        )

        print(
            "The agent will not process "
            "random or promotional articles."
        )

        return


    print(
        "\n"
        + "=" * 70
    )

    print(
        "SELECTED GLOBAL / INDIA "
        "ECONOMIC NEWS"
    )

    print(
        "=" * 70
    )


    for index, (score, article) in enumerate(
        selected,
        start=1
    ):

        title = article.get(
            "title",
            "No headline"
        )

        source = (
            article.get(
                "source",
                {}
            ).get(
                "name",
                "Unknown"
            )
        )


        print(
            f"\n{index}. {title}"
        )

        print(
            f"   Source: {source}"
        )

        print(
            f"   Relevance score: {score}"
        )


# ============================================================
# SAVE SELECTED NEWS
# ============================================================

def save_selected_news(selected):

    saved_count = 0


    for score, article in selected:

        headline = article.get(
            "title",
            "No headline"
        )


        source = (
            article.get(
                "source",
                {}
            ).get(
                "name",
                "Unknown"
            )
        )


        url = article.get(
            "url",
            ""
        )


        published_at = article.get(
            "publishedAt",
            ""
        )


        # ----------------------------------------------------
        # Do not save duplicates
        # ----------------------------------------------------

        if news_exists(url):

            print(
                f"\nAlready exists:"
                f"\n{headline}"
            )

            continue


        # ----------------------------------------------------
        # IMPORTANT:
        #
        # We are intentionally NOT calling Gemini here.
        #
        # Your existing summarizer.py / AI analysis stage
        # should process the selected articles separately.
        # ----------------------------------------------------

        analysis = (
            "Pending AI analysis"
        )


        try:

            save_news(
                headline,
                source,
                url,
                published_at,
                analysis
            )


            saved_count += 1


            print(
                f"\nSaved:"
                f"\n{headline}"
            )


        except Exception as error:

            print(
                f"\nDatabase error while "
                f"saving article:"
            )

            print(
                headline
            )

            print(
                error
            )


    return saved_count


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n"
        + "=" * 70
    )

    print(
        "BUSINESS NEWS AI AGENT"
    )

    print(
        "=" * 70
    )


    # --------------------------------------------------------
    # STEP 1: DATABASE
    # --------------------------------------------------------

    create_database()

    repair_database()


    # --------------------------------------------------------
    # STEP 2: FETCH NEWS
    # --------------------------------------------------------

    all_articles = fetch_candidate_news()


    if not all_articles:

        print(
            "\nNo articles were received "
            "from NewsAPI."
        )

        print(
            "\nProcessing stopped safely."
        )

        return


    # --------------------------------------------------------
    # STEP 3: FILTER
    # --------------------------------------------------------

    selected = filter_articles(
        all_articles
    )


    # --------------------------------------------------------
    # STEP 4: DISPLAY
    # --------------------------------------------------------

    display_selected_articles(
        selected
    )


    # --------------------------------------------------------
    # STEP 5: SAVE
    # --------------------------------------------------------

    if selected:

        saved_count = save_selected_news(
            selected
        )

        print(
            f"\nSaved {saved_count} "
            "new articles to database."
        )


    # --------------------------------------------------------
    # STEP 6: DELETE OLD NEWS
    # --------------------------------------------------------

    delete_old_news()


    # --------------------------------------------------------
    # FINISHED
    # --------------------------------------------------------

    print(
        "\n"
        + "=" * 70
    )

    print(
        "NEWS COLLECTION COMPLETE"
    )

    print(
        "=" * 70
    )


# ============================================================
# RUN PROGRAM
# ============================================================

if __name__ == "__main__":

    main()