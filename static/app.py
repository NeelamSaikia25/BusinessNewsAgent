import sqlite3
import re

from flask import Flask, render_template, request


# ============================================================
# BUSINESS NEWS AI AGENT - WEB DASHBOARD
# ============================================================

app = Flask(__name__)

DATABASE_NAME = "news.db"


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
# CHECK TABLE EXISTS
# ============================================================

def table_exists(connection, table_name):

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

    text = str(text).strip()

    text = text.replace(
        "**",
        ""
    )

    return text.strip()


# ============================================================
# EXTRACT GEMINI SECTION
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
    ).replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "**",
        ""
    )

    sections = {

        "relevance": (
            "RELEVANCE",
            [
                "SUMMARY",
                "GLOBAL IMPACT",
                "INDIA IMPACT"
            ]
        ),

        "summary": (
            "SUMMARY",
            [
                "GLOBAL IMPACT",
                "INDIA IMPACT"
            ]
        ),

        "global_impact": (
            "GLOBAL IMPACT",
            [
                "INDIA IMPACT",
                "KEY CONCEPT"
            ]
        ),

        "india_impact": (
            "INDIA IMPACT",
            [
                "KEY CONCEPT",
                "SENTIMENT"
            ]
        ),

        "key_concept": (
            "KEY CONCEPT",
            [
                "SENTIMENT",
                "SENTIMENT REASON"
            ]
        ),

        "sentiment": (
            "SENTIMENT",
            [
                "SENTIMENT REASON",
                "IMPACT LEVEL"
            ]
        ),

        "sentiment_reason": (
            "SENTIMENT REASON",
            [
                "IMPACT LEVEL",
                "IMPACT REASON"
            ]
        ),

        "impact_level": (
            "IMPACT LEVEL",
            [
                "IMPACT REASON",
                "MARKET PREDICTION"
            ]
        ),

        "impact_reason": (
            "IMPACT REASON",
            [
                "MARKET PREDICTION",
                "INDIA IMPACT SCORE"
            ]
        ),

        "market_prediction": (
            "MARKET PREDICTION",
            [
                "INDIA IMPACT SCORE"
            ]
        ),

        "india_score": (
            "INDIA IMPACT SCORE",
            []
        )
    }

    for key, (
        section_name,
        next_sections
    ) in sections.items():

        result[key] = extract_section(
            text,
            section_name,
            next_sections
        )

    # --------------------------------------------------------
    # MARKET PREDICTIONS
    # --------------------------------------------------------

    prediction_text = (
        result["market_prediction"]
    )

    for line in prediction_text.splitlines():

        line = line.strip()

        lower_line = line.lower()

        if lower_line.startswith("oil:"):

            result["oil_prediction"] = (
                line.split(
                    ":",
                    1
                )[1].strip().upper()
            )

        elif lower_line.startswith("nifty:"):

            result["nifty_prediction"] = (
                line.split(
                    ":",
                    1
                )[1].strip().upper()
            )

        elif lower_line.startswith(
            "indian energy stocks:"
        ):

            result["energy_prediction"] = (
                line.split(
                    ":",
                    1
                )[1].strip().upper()
            )

    # --------------------------------------------------------
    # INDIA SCORE
    # --------------------------------------------------------

    if result["india_score"]:

        score_match = re.search(
            r"\b(\d{1,3})\b",
            result["india_score"]
        )

        if score_match:

            result["india_score"] = (
                score_match.group(1)
            )

    # --------------------------------------------------------
    # FALLBACK SENTIMENT
    # --------------------------------------------------------

    if not result["sentiment"]:

        match = re.search(
            r"SENTIMENT\s*:?\s*"
            r"(POSITIVE|NEGATIVE|NEUTRAL)",
            text,
            re.IGNORECASE
        )

        if match:

            result["sentiment"] = (
                match.group(1).upper()
            )

    # --------------------------------------------------------
    # FALLBACK IMPACT
    # --------------------------------------------------------

    if not result["impact_level"]:

        match = re.search(
            r"IMPACT LEVEL\s*:?\s*"
            r"(HIGH|MEDIUM|LOW|NONE)",
            text,
            re.IGNORECASE
        )

        if match:

            result["impact_level"] = (
                match.group(1).upper()
            )

    return result


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

        "actual_oil": None,
        "actual_nifty": None,
        "actual_energy": None,

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
# GET 3-DAY MARKET COMPARISON
# ============================================================

def get_market_comparison(news_id):

    connection = get_connection()

    # --------------------------------------------------------
    # Confirm required tables
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # TOTAL ARTICLES
    # --------------------------------------------------------

    cursor.execute(
        "SELECT COUNT(*) FROM news"
    )

    total_articles = cursor.fetchone()[0]

    # --------------------------------------------------------
    # TOTAL PREDICTIONS
    # --------------------------------------------------------

    if table_exists(
        connection,
        "predictions"
    ):

        cursor.execute(
            "SELECT COUNT(*) FROM predictions"
        )

        total_predictions = (
            cursor.fetchone()[0]
        )

    else:

        total_predictions = 0

    # --------------------------------------------------------
    # TOTAL VALIDATED ARTICLES
    # --------------------------------------------------------

    if table_exists(
        connection,
        "market_results"
    ):

        cursor.execute(
            """
            SELECT COUNT(DISTINCT news_id)
            FROM market_results
            """
        )

        validated_articles = (
            cursor.fetchone()[0]
        )

    else:

        validated_articles = 0

    # --------------------------------------------------------
    # CALCULATE DIRECTIONAL ACCURACY
    # --------------------------------------------------------

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

            pairs = [

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

            for predicted, actual in pairs:

                if not predicted:

                    continue

                if actual == "NO DATA":

                    continue

                total_comparisons += 1

                if predicted == actual:

                    total_matches += 1

    # --------------------------------------------------------
    # ACCURACY
    # --------------------------------------------------------

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
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )