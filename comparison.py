import sqlite3


# ============================================================
# BUSINESS NEWS AGENT
# AI PREDICTION vs ACTUAL MARKET MOVEMENT
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

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
# GET VALIDATION DATA FOR ONE ARTICLE
# ============================================================

def get_validation_data(news_id):

    connection = get_connection()

    if not table_exists(
        connection,
        "news"
    ):

        connection.close()

        return None

    if not table_exists(
        connection,
        "predictions"
    ):

        connection.close()

        return None

    if not table_exists(
        connection,
        "market_results"
    ):

        connection.close()

        return None

    cursor = connection.cursor()

    try:

        cursor.execute(
            """
            SELECT

                n.id,
                n.headline,
                n.analysis,

                p.predicted_oil,
                p.predicted_nifty,
                p.predicted_energy,
                p.prediction_date,

                m.recorded_at,

                m.oil_price,
                m.nifty_value,

                m.reliance_change,
                m.ongc_change,
                m.ioc_change,
                m.bpcl_change,

                m.energy_average_change

            FROM news n

            LEFT JOIN predictions p
                ON n.id = p.news_id

            LEFT JOIN market_results m
                ON n.id = m.news_id

            WHERE n.id = ?

            ORDER BY m.id DESC

            LIMIT 1
            """,
            (news_id,)
        )

        result = cursor.fetchone()

    except sqlite3.Error as error:

        print(
            "\nDATABASE ERROR:"
        )

        print(error)

        connection.close()

        return None

    connection.close()

    return result


# ============================================================
# GET MARKET DIRECTION
# ============================================================

def get_direction(value):

    if value is None:

        return "UNKNOWN"

    try:

        value = float(value)

    except (
        TypeError,
        ValueError
    ):

        return "UNKNOWN"

    # --------------------------------------------------------
    # Threshold:
    #
    # Above +0.50% = UP
    # Below -0.50% = DOWN
    # Otherwise = FLAT
    # --------------------------------------------------------

    if value > 0.5:

        return "UP"

    if value < -0.5:

        return "DOWN"

    return "FLAT"


# ============================================================
# NORMALIZE AI PREDICTION
# ============================================================

def normalize_prediction(value):

    if value is None:

        return None

    value = str(
        value
    ).strip().upper()

    if value in [
        "UP",
        "DOWN",
        "FLAT"
    ]:

        return value

    return None


# ============================================================
# COMPARE PREDICTION WITH ACTUAL
# ============================================================

def compare_prediction(
    predicted,
    actual_value
):

    predicted_direction = (
        normalize_prediction(
            predicted
        )
    )

    if predicted_direction is None:

        return "NO PREDICTION"

    actual_direction = (
        get_direction(
            actual_value
        )
    )

    if actual_direction == "UNKNOWN":

        return "NO DATA"

    if predicted_direction == actual_direction:

        return "MATCH"

    return "MISMATCH"


# ============================================================
# FORMAT MOVEMENT
# ============================================================

def format_movement(value):

    if value is None:

        return "Data unavailable"

    try:

        value = float(value)

    except (
        TypeError,
        ValueError
    ):

        return "Data unavailable"

    direction = get_direction(
        value
    )

    return (
        f"{value:+.2f}% "
        f"({direction})"
    )


# ============================================================
# RESULT SYMBOL
# ============================================================

def result_symbol(result):

    if result == "MATCH":

        return "✓"

    if result == "MISMATCH":

        return "✗"

    return "-"


# ============================================================
# CREATE SHORT COMPARISON
# ============================================================

def create_comparison(data):

    if data is None:

        print()
        print(
            "No validation data available."
        )

        return

    print()
    print("=" * 70)

    print(
        "AI PREDICTION vs ACTUAL MARKET MOVEMENT"
    )

    print("=" * 70)

    # ========================================================
    # BASIC INFORMATION
    # ========================================================

    news_id = data["id"]

    headline = data["headline"]

    prediction_date = (
        data["prediction_date"]
    )

    recorded_at = (
        data["recorded_at"]
    )

    print()
    print(
        f"NEWS ID: {news_id}"
    )

    print(
        f"NEWS: {headline}"
    )

    print(
        f"Prediction recorded: "
        f"{prediction_date or 'N/A'}"
    )

    print(
        f"Market validated: "
        f"{recorded_at or 'N/A'}"
    )

    # ========================================================
    # AI PREDICTIONS
    # ========================================================

    predicted_oil = normalize_prediction(
        data["predicted_oil"]
    )

    predicted_nifty = normalize_prediction(
        data["predicted_nifty"]
    )

    predicted_energy = normalize_prediction(
        data["predicted_energy"]
    )

    # ========================================================
    # OIL
    # ========================================================

    oil_actual = data["oil_price"]

    oil_result = compare_prediction(
        predicted_oil,
        oil_actual
    )

    # ========================================================
    # NIFTY
    # ========================================================

    nifty_actual = data["nifty_value"]

    nifty_result = compare_prediction(
        predicted_nifty,
        nifty_actual
    )

    # ========================================================
    # ENERGY STOCKS
    # ========================================================

    energy_actual = data[
        "energy_average_change"
    ]

    energy_result = compare_prediction(
        predicted_energy,
        energy_actual
    )

    # ========================================================
    # PRINT COMPARISON
    # ========================================================

    print()
    print(
        "3-DAY COMPARISON"
    )

    print("-" * 70)

    print(
        "\nOil:"
    )

    print(
        f"  AI predicted : "
        f"{predicted_oil or 'N/A'}"
    )

    print(
        f"  Actual       : "
        f"{format_movement(oil_actual)}"
    )

    print(
        f"  Result       : "
        f"{oil_result} "
        f"{result_symbol(oil_result)}"
    )

    print(
        "\nNIFTY:"
    )

    print(
        f"  AI predicted : "
        f"{predicted_nifty or 'N/A'}"
    )

    print(
        f"  Actual       : "
        f"{format_movement(nifty_actual)}"
    )

    print(
        f"  Result       : "
        f"{nifty_result} "
        f"{result_symbol(nifty_result)}"
    )

    print(
        "\nIndian Energy Stocks:"
    )

    print(
        f"  AI predicted : "
        f"{predicted_energy or 'N/A'}"
    )

    print(
        f"  Actual       : "
        f"{format_movement(energy_actual)}"
    )

    print(
        f"  Result       : "
        f"{energy_result} "
        f"{result_symbol(energy_result)}"
    )

    # ========================================================
    # ENERGY STOCK BREAKDOWN
    # ========================================================

    print()
    print(
        "ENERGY STOCK BREAKDOWN"
    )

    print("-" * 70)

    print(
        f"Reliance : "
        f"{format_movement(data['reliance_change'])}"
    )

    print(
        f"ONGC     : "
        f"{format_movement(data['ongc_change'])}"
    )

    print(
        f"IOC      : "
        f"{format_movement(data['ioc_change'])}"
    )

    print(
        f"BPCL     : "
        f"{format_movement(data['bpcl_change'])}"
    )

    # ========================================================
    # OVERALL RESULT
    # ========================================================

    results = [

        oil_result,

        nifty_result,

        energy_result
    ]

    matches = results.count(
        "MATCH"
    )

    comparable = sum(
        result in [
            "MATCH",
            "MISMATCH"
        ]
        for result in results
    )

    print()
    print("-" * 70)

    if comparable > 0:

        accuracy = (
            matches
            / comparable
        ) * 100

        print(
            f"Overall: "
            f"{matches}/{comparable} "
            f"directional predictions matched."
        )

        print(
            f"Directional Accuracy: "
            f"{accuracy:.1f}%"
        )

    else:

        print(
            "Overall: "
            "Insufficient data for comparison."
        )

    print("=" * 70)


# ============================================================
# GET ALL VALIDATED NEWS
# ============================================================

def get_available_news():

    connection = get_connection()

    if not table_exists(
        connection,
        "news"
    ):

        connection.close()

        return []

    if not table_exists(
        connection,
        "predictions"
    ):

        connection.close()

        return []

    if not table_exists(
        connection,
        "market_results"
    ):

        connection.close()

        return []

    cursor = connection.cursor()

    try:

        cursor.execute(
            """
            SELECT

                n.id,
                n.headline,

                p.predicted_oil,
                p.predicted_nifty,
                p.predicted_energy,

                p.prediction_date,

                m.recorded_at

            FROM news n

            INNER JOIN predictions p
                ON n.id = p.news_id

            INNER JOIN market_results m
                ON n.id = m.news_id

            ORDER BY m.recorded_at DESC
            """
        )

        results = cursor.fetchall()

    except sqlite3.Error as error:

        print(
            "\nDATABASE ERROR:"
        )

        print(error)

        connection.close()

        return []

    connection.close()

    return results


# ============================================================
# GET GLOBAL PERFORMANCE
# ============================================================

def get_global_performance():

    connection = get_connection()

    cursor = connection.cursor()

    total_predictions = 0

    validated_articles = 0

    total_comparisons = 0

    total_matches = 0

    # --------------------------------------------------------
    # Prediction count
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Validation count and comparisons
    # --------------------------------------------------------

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
            SELECT COUNT(
                DISTINCT m.news_id
            )
            FROM market_results m

            INNER JOIN predictions p
                ON m.news_id = p.news_id
            """
        )

        validated_articles = (
            cursor.fetchone()[0]
        )

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
                    row["predicted_oil"],
                    row["oil_price"]
                ),

                (
                    row["predicted_nifty"],
                    row["nifty_value"]
                ),

                (
                    row["predicted_energy"],
                    row["energy_average_change"]
                )
            ]

            for predicted, actual in comparisons:

                predicted = normalize_prediction(
                    predicted
                )

                actual_direction = get_direction(
                    actual
                )

                if predicted is None:

                    continue

                if actual_direction == "UNKNOWN":

                    continue

                total_comparisons += 1

                if predicted == actual_direction:

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

        "total_predictions":
            total_predictions,

        "validated_articles":
            validated_articles,

        "total_comparisons":
            total_comparisons,

        "total_matches":
            total_matches,

        "accuracy":
            round(
                accuracy,
                1
            )
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)

    print(
        "BUSINESS NEWS AGENT"
    )

    print(
        "AI PREDICTION vs ACTUAL MARKET MOVEMENT"
    )

    print("=" * 70)

    # ========================================================
    # PERFORMANCE
    # ========================================================

    performance = (
        get_global_performance()
    )

    print()
    print(
        "GLOBAL PERFORMANCE"
    )

    print("-" * 70)

    print(
        f"AI Predictions : "
        f"{performance['total_predictions']}"
    )

    print(
        f"Validated      : "
        f"{performance['validated_articles']}"
    )

    print(
        f"Matched        : "
        f"{performance['total_matches']}/"
        f"{performance['total_comparisons']}"
    )

    print(
        f"Accuracy       : "
        f"{performance['accuracy']:.1f}%"
    )

    # ========================================================
    # VALIDATED NEWS
    # ========================================================

    available_news = (
        get_available_news()
    )

    if not available_news:

        print()
        print(
            "No validated news available yet."
        )

        print(
            "The market tracker must populate "
            "market_results first."
        )

        return

    print()
    print(
        "VALIDATED NEWS"
    )

    print("-" * 70)

    for row in available_news:

        print()

        print(
            f"ID: {row['id']}"
        )

        print(
            f"Headline: {row['headline']}"
        )

        print(
            f"AI Oil: "
            f"{normalize_prediction(row['predicted_oil']) or 'N/A'}"
        )

        print(
            f"AI NIFTY: "
            f"{normalize_prediction(row['predicted_nifty']) or 'N/A'}"
        )

        print(
            f"AI Energy: "
            f"{normalize_prediction(row['predicted_energy']) or 'N/A'}"
        )

        print(
            f"Prediction date: "
            f"{row['prediction_date'] or 'N/A'}"
        )

        print(
            f"Validated: "
            f"{row['recorded_at'] or 'N/A'}"
        )

        print(
            "-" * 60
        )

    # ========================================================
    # MOST RECENT VALIDATED ARTICLE
    # ========================================================

    latest_news_id = (
        available_news[0]["id"]
    )

    data = get_validation_data(
        latest_news_id
    )

    if data is None:

        print(
            "\nCould not retrieve "
            "validation data."
        )

        return

    create_comparison(
        data
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()