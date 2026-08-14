import sqlite3
from datetime import datetime, timedelta

import yfinance as yf


# ============================================================
# SETTINGS
# ============================================================

DATABASE_NAME = "news.db"

# Yahoo Finance tickers
TICKERS = {
    "oil": "CL=F",
    "nifty": "^NSEI",
    "reliance": "RELIANCE.NS",
    "ongc": "ONGC.NS",
    "ioc": "IOC.NS",
    "bpcl": "BPCL.NS",
}


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():

    connection = sqlite3.connect(
        DATABASE_NAME
    )

    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# CREATE MARKET RESULTS TABLE
# ============================================================

def create_market_table():

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_results (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            news_id INTEGER,

            recorded_at TEXT,

            oil_price REAL,

            nifty_value REAL,

            reliance_change REAL,

            ongc_change REAL,

            ioc_change REAL,

            bpcl_change REAL,

            energy_average_change REAL,

            FOREIGN KEY (news_id)
                REFERENCES news(id)
        )
    """)

    connection.commit()
    connection.close()

    print("market_results table is ready.")


# ============================================================
# CHECK WHETHER NEWS HAS ALREADY BEEN VALIDATED
# ============================================================

def already_validated(news_id):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id
        FROM market_results
        WHERE news_id = ?
        LIMIT 1
    """, (news_id,))

    result = cursor.fetchone()

    connection.close()

    return result is not None


# ============================================================
# GET NEWS READY FOR VALIDATION
# ============================================================

def get_news_ready_for_validation():

    connection = get_connection()
    cursor = connection.cursor()

    # We need articles at least 3 calendar days old.
    cutoff = (
        datetime.now() - timedelta(days=3)
    ).isoformat()

    cursor.execute("""
        SELECT
            n.id,
            n.headline,
            n.published_at
        FROM news n

        INNER JOIN predictions p
            ON n.id = p.news_id

        WHERE n.published_at <= ?

        ORDER BY n.published_at ASC
    """, (cutoff,))

    results = cursor.fetchall()

    connection.close()

    return results


# ============================================================
# PARSE ARTICLE DATE
# ============================================================

def parse_article_date(value):

    if not value:
        return None

    value = str(value).strip()

    # Handle Z / UTC timestamps
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:

        parsed = datetime.fromisoformat(
            value
        )

        return parsed.date()

    except ValueError:

        # Fallback for simple YYYY-MM-DD
        try:

            return datetime.strptime(
                value[:10],
                "%Y-%m-%d"
            ).date()

        except ValueError:

            return None


# ============================================================
# DOWNLOAD HISTORICAL DATA
# ============================================================

def download_history(
    ticker,
    start_date,
    end_date
):

    try:

        # Add one day to the end because Yahoo's
        # end parameter is effectively exclusive.
        end_plus_one = (
            end_date + timedelta(days=1)
        )

        data = yf.Ticker(
            ticker
        ).history(
            start=start_date,
            end=end_plus_one,
            interval="1d",
            auto_adjust=False
        )

        if data is None or data.empty:

            return None

        # Keep only Close prices.
        close = data["Close"].copy()

        # Convert timestamps to date.
        close.index = close.index.date

        # Remove duplicate dates.
        close = close[
            ~close.index.duplicated(
                keep="last"
            )
        ]

        return close

    except Exception as error:

        print(
            f"ERROR downloading {ticker}: "
            f"{error}"
        )

        return None


# ============================================================
# CALCULATE 3-TRADING-DAY OBSERVATION
# ============================================================

def get_three_day_prices(
    ticker,
    article_date
):

    today = datetime.now().date()

    # We need enough history around the article.
    start_date = (
        article_date - timedelta(days=2)
    )

    end_date = today

    prices = download_history(
        ticker,
        start_date,
        end_date
    )

    if prices is None or len(prices) == 0:

        return None

    # --------------------------------------------------------
    # Find the first available trading day on/after
    # the article date.
    # --------------------------------------------------------

    trading_dates = sorted(
        prices.index
    )

    baseline_candidates = [
        date
        for date in trading_dates
        if date >= article_date
    ]

    if not baseline_candidates:

        return None

    baseline_date = (
        baseline_candidates[0]
    )

    baseline_index = (
        trading_dates.index(
            baseline_date
        )
    )

    # Need three trading sessions AFTER baseline.
    target_index = (
        baseline_index + 3
    )

    if target_index >= len(trading_dates):

        return {
            "status": "PENDING",
            "baseline_date": baseline_date,
            "target_date": None,
            "baseline_price": float(
                prices.loc[baseline_date]
            ),
            "target_price": None,
            "change_percent": None
        }

    target_date = (
        trading_dates[target_index]
    )

    baseline_price = float(
        prices.loc[baseline_date]
    )

    target_price = float(
        prices.loc[target_date]
    )

    if baseline_price == 0:

        return None

    change_percent = (
        (
            target_price
            - baseline_price
        )
        / baseline_price
    ) * 100

    return {

        "status": "READY",

        "baseline_date":
            baseline_date,

        "target_date":
            target_date,

        "baseline_price":
            baseline_price,

        "target_price":
            target_price,

        "change_percent":
            change_percent
    }


# ============================================================
# COLLECT ALL MARKET MOVEMENTS
# ============================================================

def collect_market_data(
    article_date
):

    print()
    print(
        "Collecting market data..."
    )

    results = {}

    for name, ticker in TICKERS.items():

        print(
            f"  Downloading {name} "
            f"({ticker})..."
        )

        result = get_three_day_prices(
            ticker,
            article_date
        )

        if result is None:

            print(
                f"  {name}: unavailable"
            )

            results[name] = None

            continue

        results[name] = result

        if result["status"] == "PENDING":

            print(
                f"  {name}: "
                f"waiting for 3 trading days"
            )

        else:

            print(
                f"  {name}: "
                f"{result['change_percent']:+.2f}%"
            )

    return results


# ============================================================
# CHECK WHETHER ALL DATA IS READY
# ============================================================

def all_market_data_ready(
    results
):

    for name in TICKERS:

        result = results.get(name)

        if result is None:
            return False

        if result.get("status") != "READY":
            return False

    return True


# ============================================================
# SAVE MARKET RESULT
# ============================================================

def save_market_result(
    news_id,
    results
):

    oil_change = (
        results["oil"]["change_percent"]
    )

    nifty_change = (
        results["nifty"]["change_percent"]
    )

    reliance_change = (
        results["reliance"]["change_percent"]
    )

    ongc_change = (
        results["ongc"]["change_percent"]
    )

    ioc_change = (
        results["ioc"]["change_percent"]
    )

    bpcl_change = (
        results["bpcl"]["change_percent"]
    )

    energy_average = (
        reliance_change
        + ongc_change
        + ioc_change
        + bpcl_change
    ) / 4

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO market_results (

            news_id,
            recorded_at,

            oil_price,
            nifty_value,

            reliance_change,
            ongc_change,
            ioc_change,
            bpcl_change,

            energy_average_change
        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (

        news_id,

        datetime.now().isoformat(),

        # IMPORTANT:
        # These fields contain percentage movement,
        # not raw market prices.
        oil_change,

        nifty_change,

        reliance_change,
        ongc_change,
        ioc_change,
        bpcl_change,

        energy_average
    ))

    connection.commit()
    connection.close()

    print(
        f"\nMarket result saved "
        f"for NEWS ID {news_id}."
    )


# ============================================================
# VALIDATE ONE ARTICLE
# ============================================================

def validate_article(
    news_id,
    headline,
    published_at
):

    print()
    print("=" * 70)

    print(
        f"NEWS ID: {news_id}"
    )

    print(
        f"HEADLINE: {headline}"
    )

    print(
        f"PUBLISHED: {published_at}"
    )

    print("=" * 70)

    if already_validated(
        news_id
    ):

        print(
            "Already validated. Skipping."
        )

        return "VALIDATED"

    article_date = parse_article_date(
        published_at
    )

    if article_date is None:

        print(
            "Could not determine article date."
        )

        return "ERROR"

    print(
        f"Baseline date candidate: "
        f"{article_date}"
    )

    results = collect_market_data(
        article_date
    )

    if not all_market_data_ready(
        results
    ):

        print()
        print(
            "3-trading-day observation "
            "is not complete yet."
        )

        return "PENDING"

    # --------------------------------------------------------
    # Make sure all markets use the same target date.
    # --------------------------------------------------------

    target_dates = []

    for result in results.values():

        target_dates.append(
            result["target_date"]
        )

    if len(set(target_dates)) != 1:

        print()
        print(
            "Market calendars do not "
            "align for all instruments."
        )

        return "PENDING"

    target_date = target_dates[0]

    print()
    print(
        f"Validation date: {target_date}"
    )

    print()
    print(
        "FINAL MARKET MOVEMENT"
    )

    print("-" * 50)

    for name, result in results.items():

        print(
            f"{name.upper():10} "
            f"{result['change_percent']:+.2f}%"
        )

    save_market_result(
        news_id,
        results
    )

    return "VALIDATED"


# ============================================================
# PROCESS ALL PENDING ARTICLES
# ============================================================

def process_pending_articles():

    articles = (
        get_news_ready_for_validation()
    )

    if not articles:

        print()
        print(
            "No articles with AI predictions "
            "are currently ready for validation."
        )

        return

    print()
    print("=" * 70)

    print(
        f"Found {len(articles)} "
        f"article(s) eligible for checking."
    )

    print("=" * 70)

    validated = 0
    pending = 0
    skipped = 0
    errors = 0

    for article in articles:

        status = validate_article(
            article["id"],
            article["headline"],
            article["published_at"]
        )

        if status == "VALIDATED":
            validated += 1

        elif status == "PENDING":
            pending += 1

        elif status == "ERROR":
            errors += 1

        else:
            skipped += 1

    print()
    print("=" * 70)

    print(
        "VALIDATION SUMMARY"
    )

    print("=" * 70)

    print(
        f"Validated : {validated}"
    )

    print(
        f"Pending   : {pending}"
    )

    print(
        f"Skipped   : {skipped}"
    )

    print(
        f"Errors    : {errors}"
    )

    print("=" * 70)


# ============================================================
# DATABASE STATUS
# ============================================================

def show_database_status():

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        ORDER BY name
    """)

    tables = cursor.fetchall()

    connection.close()

    print()
    print(
        "DATABASE TABLES"
    )

    print("=" * 40)

    for table in tables:

        print(
            table["name"]
        )

    print("=" * 40)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 70)

    print(
        "BUSINESS NEWS AGENT"
    )

    print(
        "3-DAY MARKET VALIDATION ENGINE"
    )

    print("=" * 70)

    create_market_table()

    show_database_status()

    process_pending_articles()

    print()
    print(
        "Market validation run complete."
    )