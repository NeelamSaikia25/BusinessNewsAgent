import sqlite3
import json
import re
import time
from datetime import datetime

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

# Number of retries for failed requests
MAX_RETRIES = 4


# ============================================================
# GEMINI SETUP
# ============================================================

if not GEMINI_API_KEY:
    print("ERROR: GEMINI_API_KEY not found in config.py")
    raise SystemExit(1)

client = genai.Client(
    api_key=GEMINI_API_KEY
)


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

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            news_id INTEGER NOT NULL,

            predicted_oil TEXT,

            predicted_nifty TEXT,

            predicted_energy TEXT,

            prediction_date TEXT,

            UNIQUE(news_id),

            FOREIGN KEY(news_id)
            REFERENCES news(id)
        )
    """)

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
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        ORDER BY name
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
You are BusinessNewsAgent, an AI business-news
analysis system.

Analyze the following business/financial news article.

{article_text}

Determine the likely directional impact over the
next approximately 3 days.

Evaluate:

1. Crude oil prices
2. India's NIFTY index
3. Indian energy-sector stocks

For each category select exactly one:

UP
DOWN
FLAT

Use FLAT when the article does not provide enough
evidence for a meaningful directional prediction.

Do not provide investment advice.

Do not provide numerical price targets.

Return ONLY valid JSON.

Use exactly this structure:

{{
    "analysis": "brief explanation of the economic impact",
    "predicted_oil": "UP",
    "predicted_nifty": "DOWN",
    "predicted_energy": "UP"
}}
"""


# ============================================================
# GEMINI REQUEST WITH RETRIES
# ============================================================

def generate_prediction(article_text):

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

            print(
                error_text
            )

            # Wait longer after each failure
            wait_time = 3 * attempt

            print()
            print(
                f"Waiting {wait_time} seconds before retry..."
            )

            time.sleep(
                wait_time
            )

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

    cursor.execute(
        "PRAGMA table_info(news)"
    )

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

    analysis = result.get(
        "analysis",
        "No analysis provided."
    )

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
# PROCESS ALL NEWS
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