import os
import sqlite3
import re

from flask import Flask, render_template, request


# ============================================================
# BUSINESS NEWS AI AGENT - WEB DASHBOARD
# ============================================================

app = Flask(__name__)

DATABASE_NAME = os.getenv(
    "DATABASE_NAME",
    "news.db"
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
# CHECK WHETHER TABLE EXISTS
# ============================================================

def table_exists(
    connection,
    table_name
):

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name = ?
        """,
        (table_name,)
    )

    result = cursor.fetchone()

    return result is not None


# ============================================================
# GET NEWS
# ============================================================

def get_news(search=""):

    connection = get_connection()
    cursor = connection.cursor()

    if search:

        search_term = f"%{search}%"

        cursor.execute(
            """
            SELECT *
            FROM news
            WHERE
                headline LIKE ?
                OR source LIKE ?
                OR analysis LIKE ?
            ORDER BY saved_at DESC
            """,
            (
                search_term,
                search_term,
                search_term
            )
        )

    else:

        cursor.execute(
            """
            SELECT *
            FROM news
            ORDER BY saved_at DESC
            """
        )

    rows = cursor.fetchall()

    connection.close()

    return rows


# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(text):

    if not text:

        return ""

    text = str(
        text
    ).strip()

    text = text.replace(
        "**",
        ""
    )

    return text.strip()


# ============================================================
# EXTRACT STRUCTURED GEMINI SECTION
# ============================================================

def extract_section(
    text,
    section_name,
    next_sections
):

    if not text:

        return ""

    if next_sections:

        next_pattern = "|".join(
            map(
                re.escape,
                next_sections
            )
        )

        pattern = (
            rf"(?:^|\n)\s*"
            rf"(?:\d+[\.\)]\s*)?"
            rf"{re.escape(section_name)}"
            rf"\s*:?\s*"
            rf"(.*?)"
            rf"(?="
            rf"\n\s*(?:\d+[\.\)]\s*)?"
            rf"(?:{next_pattern})"
            rf"\s*:?"
            rf"|$)"
        )

    else:

        pattern = (
            rf"(?:^|\n)\s*"
            rf"(?:\d+[\.\)]\s*)?"
            rf"{re.escape(section_name)}"
            rf"\s*:?\s*"
            rf"(.*)"
            rf"$"
        )

    match = re.search(
        pattern,
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    if not match:

        return ""

    return clean_text(
        match.group(1)
    )


# ============================================================
# PARSE AI ANALYSIS
# ============================================================

def parse_analysis(analysis):

    result = {

        "relevance": "",
        "summary": "",
        "global_impact": "",
        "india_impact": "",
        "key_concept": "",
        "sentiment": "",
        "sentiment_reason": "",
        "impact_level": "",
        "impact_reason": "",
        "market_prediction": "",
        "oil_prediction": "",
        "nifty_prediction": "",
        "energy_prediction": "",
        "india_score": ""
    }

    if not analysis:

        return result

    text = str(
        analysis
    ).strip()

    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "**",
        ""
    )

    result["summary"] = clean_text(
        text
    )

    structured_summary = extract_section(
        text,
        "SUMMARY",
        [
            "GLOBAL IMPACT",
            "INDIA IMPACT"
        ]
    )

    if structured_summary:

        result["summary"] = (
            structured_summary
        )

    result["relevance"] = extract_section(
        text,
        "RELEVANCE",
        [
            "SUMMARY",
            "GLOBAL IMPACT",
            "INDIA IMPACT"
        ]
    ).upper()

    result["global_impact"] = extract_section(
        text,
        "GLOBAL IMPACT",
        [
            "INDIA IMPACT",
            "KEY CONCEPT"
        ]
    )

    result["india_impact"] = extract_section(
        text,
        "INDIA IMPACT",
        [
            "KEY CONCEPT",
            "SENTIMENT"
        ]
    )

    result["key_concept"] = extract_section(
        text,
        "KEY CONCEPT",
        [
            "SENTIMENT",
            "SENTIMENT REASON"
        ]
    )

    result["sentiment"] = extract_section(
        text,
        "SENTIMENT",
        [
            "SENTIMENT REASON",
            "IMPACT LEVEL"
        ]
    ).upper()

    result["sentiment_reason"] = extract_section(
        text,
        "SENTIMENT REASON",
        [
            "IMPACT LEVEL",
            "IMPACT REASON"
        ]
    )

    result["impact_level"] = extract_section(
        text,
        "IMPACT LEVEL",
        [
            "IMPACT REASON",
            "MARKET PREDICTION"
        ]
    ).upper()

    result["impact_reason"] = extract_section(
        text,
        "IMPACT REASON",
        [
            "MARKET PREDICTION",
            "INDIA IMPACT SCORE"
        ]
    )

    result["market_prediction"] = extract_section(
        text,
        "MARKET PREDICTION",
        [
            "INDIA IMPACT SCORE"
        ]
    )

    result["india_score"] = extract_section(
        text,
        "INDIA IMPACT SCORE",
        []
    )

    for line in result[
        "market_prediction"
    ].splitlines():

        line = line.strip()

        lower_line = line.lower()

        if lower_line.startswith(
            "oil:"
        ):

            result["oil_prediction"] = (
                line.split(
                    ":",
                    1
                )[1]
                .strip()
                .upper()
            )

        elif lower_line.startswith(
            "nifty:"
        ):

            result["nifty_prediction"] = (
                line.split(
                    ":",
                    1
                )[1]
                .strip()
                .upper()
            )

        elif lower_line.startswith(
            "indian energy stocks:"
        ):

            result["energy_prediction"] = (
                line.split(
                    ":",
                    1
                )[1]
                .strip()
                .upper()
            )

    if result["india_score"]:

        score_match = re.search(
            r"\b(\d{1,3})\b",
            result["india_score"]
        )

        if score_match:

            result["india_score"] = (
                score_match.group(1)
            )

    return result


# ============================================================
# NORMALIZE PREDICTION
# ============================================================

def normalize_prediction(value):

    if value is None:

        return ""

    value = str(
        value
    ).strip().upper()

    if value in [
        "UP",
        "DOWN",
        "FLAT"
    ]:

        return value

    return value


# ============================================================
# GET AI PREDICTION
# ============================================================

def get_ai_prediction(news_id):

    connection = get_connection()
    cursor = connection.cursor()

    if not table_exists(
        connection,
        "predictions"
    ):

        connection.close()

        return {

            "oil": "",
            "nifty": "",
            "energy": "",
            "prediction_date": None
        }

    cursor.execute(
        """
        SELECT

            predicted_oil,
            predicted_nifty,
            predicted_energy,
            prediction_date

        FROM predictions

        WHERE news_id = ?

        ORDER BY id DESC

        LIMIT 1
        """,
        (news_id,)
    )

    row = cursor.fetchone()

    connection.close()

    if not row:

        return {

            "oil": "",
            "nifty": "",
            "energy": "",
            "prediction_date": None
        }

    return {

        "oil":
            normalize_prediction(
                row["predicted_oil"]
            ),

        "nifty":
            normalize_prediction(
                row["predicted_nifty"]
            ),

        "energy":
            normalize_prediction(
                row["predicted_energy"]
            ),

        "prediction_date":
            row["prediction_date"]
    }


# ============================================================
# MARKET DIRECTION
# ============================================================

def get_direction(value):

    if value is None:

        return "NO DATA"

    try:

        value = float(value)

    except (
        TypeError,
        ValueError
    ):

        return "NO DATA"

    if value > 0.5:

        return "UP"

    if value < -0.5:

        return "DOWN"

    return "FLAT"


# ============================================================
# COMPARE PREDICTION
# ============================================================

def compare_prediction(
    predicted,
    actual
):

    predicted = normalize_prediction(
        predicted
    )

    if not predicted:

        return "NO PREDICTION"

    if actual == "NO DATA":

        return "NO DATA"

    if predicted == actual:

        return "MATCH"

    return "MISMATCH"


# ============================================================
# EMPTY COMPARISON
# ============================================================

def empty_comparison():

    return {

        "available": False,

        "predicted_oil": "",
        "predicted_nifty": "",
        "predicted_energy": "",

        "actual_oil": "NO DATA",
        "actual_nifty": "NO DATA",
        "actual_energy": "NO DATA",

        "actual_oil_percent": None,
        "actual_nifty_percent": None,
        "actual_energy_percent": None,

        "oil_result": "NO DATA",
        "nifty_result": "NO DATA",
        "energy_result": "NO DATA",

        "matches": 0,
        "comparable": 0,

        "prediction_date": None,
        "recorded_at": None
    }


# ============================================================
# GET MARKET COMPARISON
# ============================================================

def get_market_comparison(news_id):

    connection = get_connection()

    if not table_exists(
        connection,
        "predictions"
    ):

        connection.close()

        return empty_comparison()

    if not table_exists(
        connection,
        "market_results"
    ):

        connection.close()

        return empty_comparison()

    cursor = connection.cursor()

    try:

        cursor.execute(
            """
            SELECT

                p.predicted_oil,
                p.predicted_nifty,
                p.predicted_energy,
                p.prediction_date,

                m.recorded_at,
                m.oil_price,
                m.nifty_value,
                m.energy_average_change

            FROM predictions p

            LEFT JOIN market_results m
                ON p.news_id = m.news_id

            WHERE p.news_id = ?

            ORDER BY m.id DESC

            LIMIT 1
            """,
            (news_id,)
        )

        row = cursor.fetchone()

    except sqlite3.Error:

        connection.close()

        return empty_comparison()

    connection.close()

    if not row:

        return empty_comparison()

    predicted_oil = normalize_prediction(
        row["predicted_oil"]
    )

    predicted_nifty = normalize_prediction(
        row["predicted_nifty"]
    )

    predicted_energy = normalize_prediction(
        row["predicted_energy"]
    )

    actual_oil = get_direction(
        row["oil_price"]
    )

    actual_nifty = get_direction(
        row["nifty_value"]
    )

    actual_energy = get_direction(
        row["energy_average_change"]
    )

    oil_result = compare_prediction(
        predicted_oil,
        actual_oil
    )

    nifty_result = compare_prediction(
        predicted_nifty,
        actual_nifty
    )

    energy_result = compare_prediction(
        predicted_energy,
        actual_energy
    )

    results = [

        oil_result,
        nifty_result,
        energy_result
    ]

    matches = sum(
        result == "MATCH"
        for result in results
    )

    comparable = sum(
        result in [
            "MATCH",
            "MISMATCH"
        ]
        for result in results
    )

    return {

        "available":
            row["recorded_at"] is not None,

        "predicted_oil":
            predicted_oil,

        "predicted_nifty":
            predicted_nifty,

        "predicted_energy":
            predicted_energy,

        "actual_oil":
            actual_oil,

        "actual_nifty":
            actual_nifty,

        "actual_energy":
            actual_energy,

        "actual_oil_percent":
            row["oil_price"],

        "actual_nifty_percent":
            row["nifty_value"],

        "actual_energy_percent":
            row["energy_average_change"],

        "oil_result":
            oil_result,

        "nifty_result":
            nifty_result,

        "energy_result":
            energy_result,

        "matches":
            matches,

        "comparable":
            comparable,

        "prediction_date":
            row["prediction_date"],

        "recorded_at":
            row["recorded_at"]
    }


# ============================================================
# GLOBAL AGENT PERFORMANCE
# ============================================================

def get_global_stats():

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM news"
    )

    total_articles = (
        cursor.fetchone()[0]
    )

    if table_exists(
        connection,
        "predictions"
    ):

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM predictions
            """
        )

        total_predictions = (
            cursor.fetchone()[0]
        )

    else:

        total_predictions = 0

    if table_exists(
        connection,
        "market_results"
    ):

        cursor.execute(
            """
            SELECT COUNT(
                DISTINCT news_id
            )
            FROM market_results
            """
        )

        validated_articles = (
            cursor.fetchone()[0]
        )

    else:

        validated_articles = 0

    total_comparisons = 0
    total_matches = 0

    if (
        table_exists(
            connection,
            "predictions"
        )
        and
        table_exists(
            connection,
            "market_results"
        )
    ):

        cursor.execute(
            """
            SELECT

                p.predicted_oil,
                p.predicted_nifty,
                p.predicted_energy,

                m.oil_price,
                m.nifty_value,
                m.energy_average_change

            FROM predictions p

            INNER JOIN market_results m
                ON p.news_id = m.news_id
            """
        )

        rows = cursor.fetchall()

        for row in rows:

            comparisons = [

                (
                    normalize_prediction(
                        row["predicted_oil"]
                    ),
                    get_direction(
                        row["oil_price"]
                    )
                ),

                (
                    normalize_prediction(
                        row["predicted_nifty"]
                    ),
                    get_direction(
                        row["nifty_value"]
                    )
                ),

                (
                    normalize_prediction(
                        row["predicted_energy"]
                    ),
                    get_direction(
                        row["energy_average_change"]
                    )
                )
            ]

            for predicted, actual in comparisons:

                if not predicted:

                    continue

                if actual == "NO DATA":

                    continue

                total_comparisons += 1

                if predicted == actual:

                    total_matches += 1

    if total_comparisons > 0:

        accuracy = (
            total_matches
            / total_comparisons
        ) * 100

    else:

        accuracy = 0

    connection.close()

    return {

        "total_articles":
            total_articles,

        "total_predictions":
            total_predictions,

        "validated_articles":
            validated_articles,

        "total_matches":
            total_matches,

        "total_comparisons":
            total_comparisons,

        "accuracy":
            round(
                accuracy,
                1
            )
    }


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def index():

    search = request.args.get(
        "search",
        ""
    ).strip()

    rows = get_news(
        search
    )

    articles = []

    for row in rows:

        article = dict(row)

        article["analysis_data"] = (
            parse_analysis(
                article.get(
                    "analysis",
                    ""
                )
            )
        )

        article["prediction_data"] = (
            get_ai_prediction(
                article["id"]
            )
        )

        article["comparison"] = (
            get_market_comparison(
                article["id"]
            )
        )

        articles.append(
            article
        )

    global_stats = get_global_stats()

    return render_template(
        "index.html",
        articles=articles,
        search=search,
        total_articles=len(
            articles
        ),
        global_stats=global_stats
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    return {

        "status":
            "running",

        "service":
            "BusinessNewsAgent"
    }


# ============================================================
# RUN LOCALLY
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )