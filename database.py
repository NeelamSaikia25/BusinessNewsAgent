import db_compat as sqlite3
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

            id SERIAL PRIMARY KEY,

            headline TEXT NOT NULL,

            source TEXT,

            url TEXT,

            published_at TEXT,

            description TEXT,

            content TEXT,

            analysis TEXT,

            saved_at TEXT NOT NULL

        )
    """)

    # --------------------------------------------------------
    # AI PREDICTIONS TABLE
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (

            id SERIAL PRIMARY KEY,

            news_id INTEGER NOT NULL,

            predicted_oil TEXT,

            predicted_nifty TEXT,

            predicted_energy TEXT,

            prediction_date TEXT NOT NULL,

            FOREIGN KEY (news_id)
                REFERENCES news(id)

        )
    """)

    connection.commit()

    connection.close()




# ============================================================
# SAFE REPAIR FOR LEGACY DUPLICATES
# ============================================================

def _has_real_text(value):
    text = str(value or '').strip()
    return bool(text) and text.lower() not in {'none', 'null', 'n/a'}


def _prediction_quality(row):
    fields = ('predicted_oil', 'predicted_nifty', 'predicted_energy')
    filled = sum(1 for field in fields if _has_real_text(row[field]))
    prediction_date = str(row['prediction_date'] or '')
    return (filled, bool(prediction_date), prediction_date, -int(row['id']))


def _archive_prediction(cursor, prediction_id, reason):
    cursor.execute(
        """
        INSERT INTO bna_prediction_duplicate_archive (reason, original_record)
        SELECT ?, to_jsonb(p)
        FROM predictions AS p
        WHERE p.id = ?
        """,
        (reason, prediction_id),
    )


def _repair_duplicate_prediction_news_ids(cursor):
    """Keep the most complete prediction per story and archive the rest."""
    cursor.execute(
        """
        SELECT news_id, ARRAY_AGG(id ORDER BY id) AS ids
        FROM predictions
        GROUP BY news_id
        HAVING COUNT(*) > 1
        """
    )
    groups = list(cursor.fetchall())
    for group in groups:
        prediction_ids = list(group['ids'] or [])
        if len(prediction_ids) < 2:
            continue
        cursor.execute(
            """
            SELECT id, news_id, predicted_oil, predicted_nifty,
                   predicted_energy, prediction_date
            FROM predictions
            WHERE id = ANY(?)
            """,
            (prediction_ids,),
        )
        rows = list(cursor.fetchall())
        if len(rows) < 2:
            continue
        winner = max(rows, key=_prediction_quality)
        for row in rows:
            if int(row['id']) == int(winner['id']):
                continue
            _archive_prediction(
                cursor,
                row['id'],
                'Duplicate prediction row for the same news ID; retained the most complete prediction.',
            )
            cursor.execute("DELETE FROM predictions WHERE id = ?", (row['id'],))


def _repair_duplicate_news_urls(cursor, has_market_results):
    """Merge duplicate URLs without losing the removed rows or their useful links.

    A full JSON copy of each removed news row is archived first. Predictions are
    reconciled to one strongest record per story; market-result rows are retained
    and linked to the canonical story ID.
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bna_news_duplicate_archive (
            archive_id BIGSERIAL PRIMARY KEY,
            archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            canonical_news_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            original_record JSONB NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bna_prediction_duplicate_archive (
            archive_id BIGSERIAL PRIMARY KEY,
            archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reason TEXT NOT NULL,
            original_record JSONB NOT NULL
        )
        """
    )

    # Blank URLs cannot identify an article; normalise them to NULL so the unique
    # URL index can still protect real article links.
    cursor.execute(
        "UPDATE news SET url = NULL WHERE url IS NOT NULL AND BTRIM(url) = ''"
    )

    cursor.execute(
        """
        SELECT url, ARRAY_AGG(id ORDER BY id) AS ids
        FROM news
        WHERE url IS NOT NULL
        GROUP BY url
        HAVING COUNT(*) > 1
        """
    )
    duplicate_groups = list(cursor.fetchall())

    for group in duplicate_groups:
        url = group['url']
        news_ids = list(group['ids'] or [])
        if len(news_ids) < 2:
            continue

        cursor.execute(
            """
            SELECT id, headline, source, url, published_at, description,
                   content, analysis, saved_at
            FROM news
            WHERE id = ANY(?)
            """,
            (news_ids,),
        )
        rows = list(cursor.fetchall())
        if len(rows) < 2:
            continue

        def news_quality(row):
            analysis = str(row['analysis'] or '').strip()
            is_useful_analysis = bool(analysis) and 'pending ai analysis' not in analysis.lower()
            return (
                int(is_useful_analysis),
                len(str(row['content'] or '')),
                len(str(row['description'] or '')),
                int(_has_real_text(row['headline'])),
                -int(row['id']),
            )

        canonical = max(rows, key=news_quality)
        canonical_id = int(canonical['id'])

        # Merge fields from duplicate rows into the canonical record. Longest
        # descriptions/content and completed AI analysis are preferred.
        def first_nonempty(field):
            for row in sorted(rows, key=news_quality, reverse=True):
                value = row[field]
                if _has_real_text(value):
                    return value
            return None

        def longest_text(field):
            values = [row[field] for row in rows if _has_real_text(row[field])]
            return max(values, key=lambda value: len(str(value))) if values else None

        analyses = [
            row['analysis'] for row in rows
            if _has_real_text(row['analysis'])
            and 'pending ai analysis' not in str(row['analysis']).lower()
        ]
        best_analysis = max(analyses, key=lambda value: len(str(value))) if analyses else first_nonempty('analysis')
        cursor.execute(
            """
            UPDATE news
            SET headline = ?,
                source = ?,
                published_at = ?,
                description = ?,
                content = ?,
                analysis = ?
            WHERE id = ?
            """,
            (
                first_nonempty('headline'),
                first_nonempty('source'),
                first_nonempty('published_at'),
                longest_text('description'),
                longest_text('content'),
                best_analysis,
                canonical_id,
            ),
        )

        # Preserve every removed news row as JSON before changing references.
        for row in rows:
            duplicate_id = int(row['id'])
            if duplicate_id == canonical_id:
                continue
            cursor.execute(
                """
                INSERT INTO bna_news_duplicate_archive
                    (canonical_news_id, reason, original_record)
                SELECT ?, ?, to_jsonb(n)
                FROM news AS n
                WHERE n.id = ?
                """,
                (
                    canonical_id,
                    f'Duplicate URL archived during safe repair: {url}',
                    duplicate_id,
                ),
            )

        # Re-point all historical market measurements to the retained article.
        if has_market_results:
            cursor.execute(
                "UPDATE market_results SET news_id = ? WHERE news_id = ANY(?)",
                (canonical_id, news_ids),
            )

        # If several duplicate stories have predictions, keep the most complete
        # prediction and archive the other rows before consolidating the ID.
        cursor.execute(
            """
            SELECT id, news_id, predicted_oil, predicted_nifty,
                   predicted_energy, prediction_date
            FROM predictions
            WHERE news_id = ANY(?)
            """,
            (news_ids,),
        )
        prediction_rows = list(cursor.fetchall())
        if prediction_rows:
            winner = max(
                prediction_rows,
                key=lambda row: (
                    *_prediction_quality(row),
                    int(int(row['news_id']) == canonical_id),
                ),
            )
            winner_id = int(winner['id'])
            for prediction in prediction_rows:
                if int(prediction['id']) == winner_id:
                    continue
                _archive_prediction(
                    cursor,
                    prediction['id'],
                    f'Duplicate-URL prediction consolidated into news ID {canonical_id}.',
                )
                cursor.execute("DELETE FROM predictions WHERE id = ?", (prediction['id'],))
            cursor.execute(
                "UPDATE predictions SET news_id = ? WHERE id = ?",
                (canonical_id, winner_id),
            )

        # Other FK-backed rows are preserved by moving their links before deleting
        # the duplicate news records. Only exact duplicate URLs are consolidated.
        cursor.execute(
            "DELETE FROM news WHERE id = ANY(?) AND id <> ?",
            (news_ids, canonical_id),
        )

    # This is also needed for legacy databases which accumulated multiple
    # predictions per single (non-duplicate) news row before unique indexes existed.
    _repair_duplicate_prediction_news_ids(cursor)


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
        """
        SELECT column_name AS name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'news'
        """
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

        "description": "TEXT",

        "content": "TEXT",

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

            id SERIAL PRIMARY KEY,

            news_id INTEGER NOT NULL,

            predicted_oil TEXT,

            predicted_nifty TEXT,

            predicted_energy TEXT,

            prediction_date TEXT NOT NULL,

            FOREIGN KEY (news_id)
                REFERENCES news(id)

        )
    """)

    # Older migrations/imports may have introduced duplicate records before the
    # unique indexes existed. Archive and safely consolidate those records first.
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'market_results') AS present"
    )
    market_table = cursor.fetchone()
    has_market_results = bool(market_table and market_table['present'])
    _repair_duplicate_news_urls(cursor, has_market_results)

    # Create uniqueness constraints only after legacy duplicates are resolved.
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_news_url ON news (url)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_predictions_news_id ON predictions (news_id)"
    )

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
    analysis,
    description="",
    content=""
):

    connection = get_connection()

    cursor = connection.cursor()

    saved_at = datetime.now().isoformat()

    cursor.execute(
        """
        INSERT INTO news
        (
            headline,
            source,
            url,
            published_at,
            description,
            content,
            analysis,
            saved_at
        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (url) DO NOTHING
        """,

        (
            headline,
            source,
            url,
            published_at,
            (description or "")[:4000],
            (content or "")[:4000],
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
        INSERT INTO predictions
        (
            news_id,
            predicted_oil,
            predicted_nifty,
            predicted_energy,
            prediction_date
        )

        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (news_id) DO UPDATE SET
            predicted_oil = EXCLUDED.predicted_oil,
            predicted_nifty = EXCLUDED.predicted_nifty,
            predicted_energy = EXCLUDED.predicted_energy,
            prediction_date = EXCLUDED.prediction_date
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
            headline ILIKE ?
            OR analysis ILIKE ?
            OR source ILIKE ?

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