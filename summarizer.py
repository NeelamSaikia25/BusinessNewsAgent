import db_compat as sqlite3
import json
import re
import time
from datetime import datetime, timedelta

from google import genai

try:
    from config import GEMINI_API_KEY
except ImportError:
    GEMINI_API_KEY = None


# ============================================================
# SETTINGS
# ============================================================

DATABASE_NAME = "news.db"

MODEL_NAME = "gemini-3.6-flash"

# Delay between Gemini requests
REQUEST_DELAY = 2.5

# One request attempt per article. Failed stories stay pending for a later run.
# In particular, a quota/rate-limit error must never be retried immediately.
MAX_RETRIES = 1


# ============================================================
# GEMINI SETUP
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def _is_quota_or_rate_limit_error(error):
    text = str(error).lower()
    markers = (
        "429", "resource_exhausted", "quota", "rate limit", "rate-limit",
        "too many requests", "requests per day", "requests per minute",
        "request limit", "limit exceeded",
    )
    return any(marker in text for marker in markers)


def _provider_day_key():
    # Google's Gemini API daily limits reset at midnight Pacific Time.
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def _quota_blocked_today():
    """Return True after a quota/rate-limit response, avoiding repeat calls today."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cursor.execute("SELECT value FROM app_metadata WHERE key = ?", ("gemini_quota_blocked_on",))
        row = cursor.fetchone()
        return bool(row and str(row["value"]) == _provider_day_key())
    finally:
        connection.close()


def _block_gemini_until_provider_day_changes():
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cursor.execute(
            "INSERT INTO app_metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
            ("gemini_quota_blocked_on", _provider_day_key()),
        )
        connection.commit()
    finally:
        connection.close()


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
# CREATE PREDICTIONS TABLE
# ============================================================

def create_predictions_table():

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (

            id SERIAL PRIMARY KEY,

            news_id INTEGER NOT NULL,

            predicted_oil TEXT,

            predicted_nifty TEXT,

            predicted_energy TEXT,

            prediction_date TEXT,

            FOREIGN KEY(news_id)
            REFERENCES news(id)
        )
    """)

    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_predictions_news_id ON predictions (news_id)"
    )

    connection.commit()
    connection.close()


# ============================================================
# DATABASE STATUS
# ============================================================

def show_database_status():

    connection = get_connection()
    cursor = connection.cursor()

    print()
    print("=" * 70)
    print("DATABASE STATUS")
    print("=" * 70)

    cursor.execute("""
        SELECT table_name AS name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)

    tables = cursor.fetchall()

    for table in tables:

        print(
            "TABLE:",
            table["name"]
        )

    cursor.execute("""
        SELECT COUNT(*) AS count
        FROM news
    """)

    news_count = cursor.fetchone()["count"]

    cursor.execute("""
        SELECT COUNT(*) AS count
        FROM predictions
    """)

    prediction_count = cursor.fetchone()["count"]

    print()
    print(
        "TOTAL NEWS:",
        news_count
    )

    print(
        "TOTAL PREDICTIONS:",
        prediction_count
    )

    connection.close()

    print("=" * 70)


# ============================================================
# CHECK PREDICTION
# ============================================================

def prediction_exists(news_id):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id
        FROM predictions
        WHERE news_id = ?
        LIMIT 1
    """, (news_id,))

    result = cursor.fetchone()

    connection.close()

    return result is not None


# ============================================================
# GET ARTICLE TEXT
# ============================================================

def get_article_text(row):

    headline = ""

    description = ""

    content = ""

    analysis = ""

    if "headline" in row.keys():

        headline = row["headline"] or ""

    if "description" in row.keys():

        description = row["description"] or ""

    if "content" in row.keys():

        content = row["content"] or ""

    if "analysis" in row.keys():

        analysis = row["analysis"] or ""

    article_text = f"""
HEADLINE:
{headline}

DESCRIPTION:
{description}

CONTENT:
{content}

EXISTING ANALYSIS:
{analysis}
"""

    # Prevent excessively large requests
    return article_text[:10000]


# ============================================================
# CLEAN JSON
# ============================================================

def clean_json_response(text):

    if not text:

        raise ValueError(
            "Gemini returned an empty response."
        )

    text = text.strip()

    # Remove markdown fences
    text = re.sub(
        r"^```json\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"^```\s*",
        "",
        text
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    text = text.strip()

    # Extract JSON object
    start = text.find("{")

    end = text.rfind("}")

    if start == -1 or end == -1:

        raise ValueError(
            "Gemini response did not contain valid JSON."
        )

    return text[start:end + 1]


# ============================================================
# GEMINI PROMPT
# ============================================================

def build_prompt(article_text):
    return f"""
You are BusinessNewsAgent, an evidence-focused business-news analyst and MBA learning assistant.
Analyze ONLY the information in the article text below. Do not invent facts, figures, quotations, companies, or causes. If a detail is not supported, state that it is not clear from the article.

{article_text}

Return ONLY valid JSON with exactly these keys:
{{
  "summary": "Factual 1-2 sentence explanation of what happened",
  "global_impact": "2-3 concise sentences on plausible global business/economic implications; distinguish inference from facts",
  "india_impact": "2-3 concise sentences on plausible implications for India, or say the connection is limited if not material",
  "key_concept": "One directly relevant MBA/business concept with a short explanation; if none is supported, say No clear concept",
  "sentiment": "POSITIVE, NEGATIVE, NEUTRAL, or MIXED",
  "sentiment_reason": "One concise sentence grounded in the report",
  "impact_level": "HIGH, MEDIUM, or LOW",
  "impact_reason": "One concise sentence explaining the impact rating",
  "india_impact_score": 0,
  "predicted_oil": "UP, DOWN, or FLAT",
  "predicted_nifty": "UP, DOWN, or FLAT",
  "predicted_energy": "UP, DOWN, or FLAT"
}}

Rules:
- Use a 3-day directional horizon for the three market outputs.
- Use FLAT when there is no credible, material connection to a market or evidence is insufficient.
- Do not make stock-price targets or investment recommendations.
- india_impact_score must be an integer from 0 to 100, based on the likely relevance to India's economy.
- Keep every field concise, readable, and specific to this story.
"""


# ============================================================
# GEMINI REQUEST WITH RETRIES
# ============================================================

def generate_prediction(article_text):

    if client is None:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    prompt = build_prompt(
        article_text
    )

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            print(
                f"Gemini attempt {attempt}/{MAX_RETRIES}..."
            )

            response = client.models.generate_content(

                model=MODEL_NAME,

                contents=prompt
            )

            if not response:

                raise ValueError(
                    "Empty Gemini response."
                )

            response_text = response.text

            if not response_text:

                raise ValueError(
                    "Gemini returned no text."
                )

            cleaned = clean_json_response(
                response_text
            )

            result = json.loads(
                cleaned
            )

            return result

        except Exception as error:

            last_error = error

            error_text = str(error)

            print()
            print(
                "Gemini request failed:"
            )

            print(error_text)

            if _is_quota_or_rate_limit_error(error):
                # Do not retry a known quota/rate-limit response.
                raise RuntimeError(
                    "Gemini quota/rate limit reached; stopping further calls for this provider day."
                ) from error

            # Only one attempt is allowed per article. Leave failures pending for later.
            if attempt >= MAX_RETRIES:
                break

            wait_time = 3 * attempt
            print(f"Waiting {wait_time} seconds before retry...")
            time.sleep(wait_time)

    raise RuntimeError(
        f"Gemini failed after {MAX_RETRIES} attempts: "
        f"{last_error}"
    )


# ============================================================
# NORMALIZE PREDICTION
# ============================================================

def normalize_prediction(value):

    if value is None:

        return "FLAT"

    value = str(
        value
    ).strip().upper()

    if value not in [
        "UP",
        "DOWN",
        "FLAT"
    ]:

        return "FLAT"

    return value


# ============================================================
# SAVE ANALYSIS
# ============================================================

def save_analysis(
    news_id,
    analysis
):

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute("""
        SELECT column_name AS name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'news'
    """)

    columns = [
        row["name"]
        for row in cursor.fetchall()
    ]

    if "analysis" in columns:

        cursor.execute("""
            UPDATE news
            SET analysis = ?
            WHERE id = ?
        """, (
            analysis,
            news_id
        ))

        connection.commit()

    connection.close()


# ============================================================
# SAVE PREDICTION
# ============================================================

def save_prediction(
    news_id,
    predicted_oil,
    predicted_nifty,
    predicted_energy
):

    connection = get_connection()

    cursor = connection.cursor()

    prediction_date = (
        datetime.now().isoformat()
    )

    cursor.execute("""
        INSERT INTO predictions (

            news_id,
            predicted_oil,
            predicted_nifty,
            predicted_energy,
            prediction_date

        )

        VALUES (?, ?, ?, ?, ?)

        ON CONFLICT(news_id)

        DO UPDATE SET

            predicted_oil =
                excluded.predicted_oil,

            predicted_nifty =
                excluded.predicted_nifty,

            predicted_energy =
                excluded.predicted_energy,

            prediction_date =
                excluded.prediction_date
    """, (
        news_id,
        predicted_oil,
        predicted_nifty,
        predicted_energy,
        prediction_date
    ))

    connection.commit()

    connection.close()


# ============================================================
# PROCESS ONE ARTICLE
# ============================================================

def process_article(row):

    news_id = row["id"]

    headline = row["headline"]

    print()
    print("=" * 70)

    print(
        f"PROCESSING NEWS ID: {news_id}"
    )

    print(
        f"HEADLINE: {headline}"
    )

    print("=" * 70)

    article_text = get_article_text(
        row
    )

    result = generate_prediction(
        article_text
    )

    # Store a structured analysis format that the website can display as
    # summary, global impact, India impact, sentiment, and market outlook.
    if result.get("summary") or result.get("global_impact") or result.get("india_impact"):
        market_lines = [
            f"Oil: {normalize_prediction(result.get('predicted_oil'))}",
            f"NIFTY: {normalize_prediction(result.get('predicted_nifty'))}",
            f"Energy: {normalize_prediction(result.get('predicted_energy'))}",
        ]
        impact_score = result.get("india_impact_score", 0)
        try:
            impact_score = max(0, min(100, int(impact_score)))
        except (TypeError, ValueError):
            impact_score = 0
        analysis = "\n".join([
            f"SUMMARY: {result.get('summary') or 'Not clear from the article.'}",
            f"GLOBAL IMPACT: {result.get('global_impact') or 'Not clear from the article.'}",
            f"INDIA IMPACT: {result.get('india_impact') or 'The direct connection to India is limited or not established.'}",
            f"KEY CONCEPT: {result.get('key_concept') or 'No clear concept'}",
            f"SENTIMENT: {result.get('sentiment') or 'NEUTRAL'}",
            f"SENTIMENT REASON: {result.get('sentiment_reason') or 'Not clearly established from the available article text.'}",
            f"IMPACT LEVEL: {result.get('impact_level') or 'LOW'}",
            f"IMPACT REASON: {result.get('impact_reason') or 'The article provides limited evidence of wider impact.'}",
            "MARKET PREDICTION:",
            *market_lines,
            f"INDIA IMPACT SCORE: {impact_score}",
        ])
    else:
        analysis = result.get("analysis", "No analysis provided.")

    predicted_oil = normalize_prediction(
        result.get(
            "predicted_oil"
        )
    )

    predicted_nifty = normalize_prediction(
        result.get(
            "predicted_nifty"
        )
    )

    predicted_energy = normalize_prediction(
        result.get(
            "predicted_energy"
        )
    )

    # Save AI analysis
    save_analysis(
        news_id,
        analysis
    )

    # Save prediction
    save_prediction(

        news_id,

        predicted_oil,

        predicted_nifty,

        predicted_energy
    )

    print()
    print("AI ANALYSIS:")
    print(analysis)

    print()
    print(
        "OIL     :",
        predicted_oil
    )

    print(
        "NIFTY   :",
        predicted_nifty
    )

    print(
        "ENERGY  :",
        predicted_energy
    )

    print()
    print(
        "SUCCESS: Prediction saved."
    )


# ============================================================
# PROCESS RECENT UNANALYSED ARTICLES FOR WEB REFRESH
# ============================================================

def process_recent_pending_news(hours=72, max_articles=10):
    """Analyse up to max_articles of the newest pending stories, within this run's cap.

    Each article gets at most one Gemini API attempt. A quota/rate-limit response
    stops the rest of the run and blocks further Gemini attempts until the next
    provider day; NewsAPI fetching remains independent of this Gemini block.
    """
    if client is None:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    if _quota_blocked_today():
        print("Gemini calls skipped: a quota/rate-limit response already occurred today (Pacific Time).")
        return {"selected": 0, "processed": 0, "failed": 0, "quota_limited": True}

    cutoff = (datetime.utcnow() - timedelta(hours=max(1, int(hours)))).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("""
            SELECT n.*
            FROM news n
            WHERE n.published_at >= ?
              AND (n.analysis IS NULL OR TRIM(n.analysis) = '' OR LOWER(TRIM(n.analysis)) = 'pending ai analysis')
              AND NOT EXISTS (SELECT 1 FROM predictions p WHERE p.news_id = n.id)
            ORDER BY n.published_at DESC
            LIMIT ?
        """, (cutoff, max(1, min(int(max_articles), 10))))
        articles = cursor.fetchall()
    finally:
        connection.close()

    result = {"selected": len(articles), "processed": 0, "failed": 0, "quota_limited": False}
    for index, row in enumerate(articles, start=1):
        try:
            process_article(row)
            result["processed"] += 1
        except Exception as error:
            result["failed"] += 1
            print(f"Recent article analysis failed for news ID {row['id']}: {error}")
            if _is_quota_or_rate_limit_error(error):
                result["quota_limited"] = True
                _block_gemini_until_provider_day_changes()
                break
        if index < len(articles) and not result["quota_limited"]:
            time.sleep(REQUEST_DELAY)
    return result

# ============================================================
# PROCESS ALL NEWS (CLI / MANUAL BACKFILL)
# ============================================================

def process_news():

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute("""
        SELECT *
        FROM news
        ORDER BY published_at DESC
    """)

    articles = cursor.fetchall()

    connection.close()

    if not articles:

        print(
            "No news articles found."
        )

        return

    print()
    print("=" * 70)
    print("STARTING AI PREDICTION PROCESS")
    print("=" * 70)

    print(
        "Articles found:",
        len(articles)
    )

    successful = 0

    failed = 0

    skipped = 0

    for index, row in enumerate(
        articles,
        start=1
    ):

        news_id = row["id"]

        print()
        print(
            f"ARTICLE {index}/{len(articles)}"
        )

        # ----------------------------------------------------
        # Already completed?
        # ----------------------------------------------------

        if prediction_exists(
            news_id
        ):

            skipped += 1

            print(
                f"SKIPPED: News ID {news_id} "
                f"already has prediction."
            )

            continue

        # ----------------------------------------------------
        # Process
        # ----------------------------------------------------

        try:

            process_article(
                row
            )

            successful += 1

        except Exception as error:

            failed += 1

            print()
            print(
                "FAILED:"
            )

            print(
                f"News ID: {news_id}"
            )

            print(
                f"Error: {error}"
            )

        # ----------------------------------------------------
        # IMPORTANT:
        # Give API time before next request
        # ----------------------------------------------------

        if index < len(articles):

            print()
            print(
                f"Waiting {REQUEST_DELAY} seconds..."
            )

            time.sleep(
                REQUEST_DELAY
            )

    print()
    print("=" * 70)
    print("PREDICTION PROCESS COMPLETE")
    print("=" * 70)

    print(
        "Successful:",
        successful
    )

    print(
        "Failed:",
        failed
    )

    print(
        "Skipped:",
        skipped
    )

    print("=" * 70)


# ============================================================
# VERIFY DATABASE
# ============================================================

def verify_predictions():

    connection = get_connection()

    cursor = connection.cursor()

    print()
    print("=" * 70)
    print("DATABASE VERIFICATION")
    print("=" * 70)

    cursor.execute("""
        SELECT COUNT(*) AS count
        FROM predictions
    """)

    total = cursor.fetchone()["count"]

    print(
        "TOTAL PREDICTIONS:",
        total
    )

    if total > 0:

        cursor.execute("""
            SELECT
                news_id,
                predicted_oil,
                predicted_nifty,
                predicted_energy,
                prediction_date
            FROM predictions
            ORDER BY id DESC
            LIMIT 10
        """)

        rows = cursor.fetchall()

        print()
        print("LATEST PREDICTIONS")
        print("-" * 70)

        for row in rows:

            print(
                f"NEWS ID: {row['news_id']}"
            )

            print(
                f"OIL: {row['predicted_oil']}"
            )

            print(
                f"NIFTY: {row['predicted_nifty']}"
            )

            print(
                f"ENERGY: {row['predicted_energy']}"
            )

            print(
                f"DATE: {row['prediction_date']}"
            )

            print("-" * 70)

    connection.close()

    print("=" * 70)


# ============================================================
# TEST GEMINI
# ============================================================

def test_gemini():

    if client is None:
        print("GEMINI_API_KEY is not configured.")
        return False

    print()
    print("=" * 70)
    print("TESTING GEMINI CONNECTION")
    print("=" * 70)

    try:

        response = client.models.generate_content(

            model=MODEL_NAME,

            contents=(
                "Reply with exactly this word: "
                "CONNECTED"
            )
        )

        text = (
            response.text.strip()
            if response.text
            else ""
        )

        print()
        print(
            "Gemini response:",
            text
        )

        if text:

            print()
            print(
                "GEMINI CONNECTION: SUCCESS"
            )

            return True

        print()
        print(
            "GEMINI CONNECTION: FAILED"
        )

        return False

    except Exception as error:

        print()
        print(
            "GEMINI CONNECTION: FAILED"
        )

        print()
        print(
            "ERROR:"
        )

        print(
            error
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("BUSINESS NEWS AGENT")
    print("GEMINI AI PREDICTION ENGINE")
    print("=" * 70)

    # Create database table
    create_predictions_table()

    # Show current database
    show_database_status()

    # Test Gemini
    if not test_gemini():

        print()
        print(
            "Gemini connection failed."
        )

        print(
            "Stopping prediction process."
        )

        return

    # Process news
    process_news()

    # Verify results
    verify_predictions()

    print()
    print("=" * 70)
    print("GEMINI PREDICTION RUN COMPLETE")
    print("=" * 70)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()