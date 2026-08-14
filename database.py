import sqlite3
from datetime import datetime, timedelta


# ============================================================
# DATABASE SETTINGS
# ============================================================

DATABASE_NAME = "news.db"

RETENTION_DAYS = 120


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():

    connection = sqlite3.connect(DATABASE_NAME)

    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# CREATE DATABASE
# ============================================================

def create_database():

    connection = get_connection()

    cursor = connection.cursor()

    # --------------------------------------------------------
    # NEWS TABLE
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            headline TEXT NOT NULL,

            source TEXT,

            url TEXT UNIQUE,

            published_at TEXT,

            analysis TEXT,

            saved_at TEXT NOT NULL

        )
    """)

    # --------------------------------------------------------
    # AI PREDICTIONS TABLE
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            news_id INTEGER NOT NULL,

            predicted_oil TEXT,

            predicted_nifty TEXT,

            predicted_energy TEXT,

            prediction_date TEXT NOT NULL,

            FOREIGN KEY (news_id)
                REFERENCES news(id),

            UNIQUE(news_id)

        )
    """)

    connection.commit()

    connection.close()


# ============================================================
# CHECK / REPAIR DATABASE STRUCTURE
# ============================================================

def repair_database():

    connection = get_connection()

    cursor = connection.cursor()

    # --------------------------------------------------------
    # CHECK NEWS TABLE
    # --------------------------------------------------------

    cursor.execute(
        "PRAGMA table_info(news)"
    )

    columns = {
        row["name"]
        for row in cursor.fetchall()
    }

    required_columns = {

        "headline": "TEXT",

        "source": "TEXT",

        "url": "TEXT",

        "published_at": "TEXT",

        "analysis": "TEXT",

        "saved_at": "TEXT"

    }

    for column, data_type in required_columns.items():

        if column not in columns:

            cursor.execute(
                f"""
                ALTER TABLE news
                ADD COLUMN {column} {data_type}
                """
            )

    # --------------------------------------------------------
    # MAKE SURE PREDICTIONS TABLE EXISTS
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            news_id INTEGER NOT NULL,

            predicted_oil TEXT,

            predicted_nifty TEXT,

            predicted_energy TEXT,

            prediction_date TEXT NOT NULL,

            FOREIGN KEY (news_id)
                REFERENCES news(id),

            UNIQUE(news_id)

        )
    """)

    connection.commit()

    connection.close()


# ============================================================
# CHECK IF NEWS ALREADY EXISTS
# ============================================================

def news_exists(url):

    if not url:

        return False

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM news
        WHERE url = ?
        LIMIT 1
        """,
        (url,)
    )

    result = cursor.fetchone()

    connection.close()

    return result is not None


# ============================================================
# SAVE NEWS
# ============================================================

def save_news(
    headline,
    source,
    url,
    published_at,
    analysis
):

    connection = get_connection()

    cursor = connection.cursor()

    saved_at = datetime.now().isoformat()

    cursor.execute(
        """
        INSERT OR IGNORE INTO news
        (
            headline,
            source,
            url,
            published_at,
            analysis,
            saved_at
        )

        VALUES (?, ?, ?, ?, ?, ?)
        """,

        (
            headline,
            source,
            url,
            published_at,
            analysis,
            saved_at
        )
    )

    connection.commit()

    connection.close()


# ============================================================
# GET NEWS ID BY URL
# ============================================================

def get_news_id(url):

    if not url:

        return None

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM news
        WHERE url = ?
        LIMIT 1
        """,
        (url,)
    )

    result = cursor.fetchone()

    connection.close()

    if result:

        return result["id"]

    return None


# ============================================================
# SAVE AI PREDICTION
# ============================================================

def save_prediction(
    news_id,
    predicted_oil,
    predicted_nifty,
    predicted_energy
):

    if news_id is None:

        return False

    connection = get_connection()

    cursor = connection.cursor()

    prediction_date = datetime.now().isoformat()

    cursor.execute(
        """
        INSERT OR REPLACE INTO predictions
        (
            news_id,
            predicted_oil,
            predicted_nifty,
            predicted_energy,
            prediction_date
        )

        VALUES (?, ?, ?, ?, ?)
        """,

        (
            news_id,
            predicted_oil,
            predicted_nifty,
            predicted_energy,
            prediction_date
        )
    )

    connection.commit()

    connection.close()

    return True


# ============================================================
# CHECK IF PREDICTION EXISTS
# ============================================================

def prediction_exists(news_id):

    if news_id is None:

        return False

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM predictions
        WHERE news_id = ?
        LIMIT 1
        """,
        (news_id,)
    )

    result = cursor.fetchone()

    connection.close()

    return result is not None


# ============================================================
# GET AI PREDICTION
# ============================================================

def get_prediction(news_id):

    if news_id is None:

        return None

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            news_id,
            predicted_oil,
            predicted_nifty,
            predicted_energy,
            prediction_date

        FROM predictions

        WHERE news_id = ?

        LIMIT 1
        """,
        (news_id,)
    )

    result = cursor.fetchone()

    connection.close()

    return result


# ============================================================
# GET NUMBER OF STORED ARTICLES
# ============================================================

def get_news_count():

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM news
        """
    )

    count = cursor.fetchone()[0]

    connection.close()

    return count


# ============================================================
# GET RECENT NEWS
# ============================================================

def get_recent_news(limit=20):

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            headline,
            source,
            url,
            published_at,
            analysis,
            saved_at

        FROM news

        ORDER BY saved_at DESC

        LIMIT ?
        """,

        (limit,)
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# ============================================================
# SEARCH NEWS
# ============================================================

def search_news(keyword):

    connection = get_connection()

    cursor = connection.cursor()

    search_term = f"%{keyword}%"

    cursor.execute(
        """
        SELECT
            id,
            headline,
            source,
            url,
            published_at,
            analysis,
            saved_at

        FROM news

        WHERE
            headline LIKE ?
            OR analysis LIKE ?
            OR source LIKE ?

        ORDER BY saved_at DESC
        """,

        (
            search_term,
            search_term,
            search_term
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return rows


# ============================================================
# DELETE NEWS OLDER THAN 120 DAYS
# ============================================================

def delete_old_news():

    connection = get_connection()

    cursor = connection.cursor()

    cutoff_date = (
        datetime.now()
        - timedelta(days=RETENTION_DAYS)
    )

    # --------------------------------------------------------
    # FIRST DELETE PREDICTIONS BELONGING TO OLD NEWS
    # --------------------------------------------------------

    cursor.execute(
        """
        DELETE FROM predictions

        WHERE news_id IN (

            SELECT id
            FROM news
            WHERE saved_at < ?

        )
        """,
        (
            cutoff_date.isoformat(),
        )
    )

    # --------------------------------------------------------
    # THEN DELETE OLD NEWS
    # --------------------------------------------------------

    cursor.execute(
        """
        DELETE FROM news

        WHERE saved_at < ?
        """,

        (
            cutoff_date.isoformat(),
        )
    )

    deleted_count = cursor.rowcount

    connection.commit()

    connection.close()

    if deleted_count > 0:

        print(
            f"\nDeleted {deleted_count} "
            f"articles older than "
            f"{RETENTION_DAYS} days."
        )


# ============================================================
# DATABASE STATISTICS
# ============================================================

def database_stats():

    connection = get_connection()

    cursor = connection.cursor()

    # --------------------------------------------------------
    # TOTAL NEWS
    # --------------------------------------------------------

    cursor.execute(
        "SELECT COUNT(*) FROM news"
    )

    total = cursor.fetchone()[0]

    # --------------------------------------------------------
    # TOTAL PREDICTIONS
    # --------------------------------------------------------

    cursor.execute(
        "SELECT COUNT(*) FROM predictions"
    )

    predictions = cursor.fetchone()[0]

    # --------------------------------------------------------
    # OLDEST ARTICLE
    # --------------------------------------------------------

    cursor.execute(
        """
        SELECT MIN(saved_at)
        FROM news
        """
    )

    oldest = cursor.fetchone()[0]

    # --------------------------------------------------------
    # NEWEST ARTICLE
    # --------------------------------------------------------

    cursor.execute(
        """
        SELECT MAX(saved_at)
        FROM news
        """
    )

    newest = cursor.fetchone()[0]

    connection.close()

    return {

        "total_articles": total,

        "total_predictions": predictions,

        "oldest_article": oldest,

        "newest_article": newest

    }


# ============================================================
# INITIAL DATABASE SETUP
# ============================================================

create_database()

repair_database()