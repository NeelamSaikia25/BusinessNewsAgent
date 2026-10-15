import os
import db_compat as sqlite3
import re
import json
import hashlib
import hmac
import secrets
import random
import threading
from datetime import datetime, timedelta, date, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    session, abort, jsonify
)


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
# NEWS REFRESH STATUS + BACKGROUND WORKER
# ============================================================

# Refresh no more than once per six-hour window, even when a visitor clicks manually.
NEWS_REFRESH_INTERVAL_HOURS = 6
NEWS_REFRESH_COOLDOWN_SECONDS = 6 * 60 * 60
NEWS_AUTO_REFRESH = os.getenv("NEWS_AUTO_REFRESH", "true").strip().lower() not in {"0", "false", "no", "off"}
_NEWS_REFRESH_LOCK = threading.Lock()

def _utc_now_text():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _ensure_refresh_metadata_table():
    connection = get_connection()
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.commit()
    finally:
        connection.close()

def _set_refresh_metadata(**values):
    _ensure_refresh_metadata_table()
    connection = get_connection()
    try:
        for key, value in values.items():
            connection.execute(
                "INSERT INTO app_metadata (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
                (key, str(value if value is not None else ""))
            )
        connection.commit()
    finally:
        connection.close()

def _get_refresh_metadata():
    _ensure_refresh_metadata_table()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT key, value FROM app_metadata WHERE key LIKE 'news_refresh_%'")
        return {row["key"]: row["value"] for row in cursor.fetchall()}
    finally:
        connection.close()

def _parse_refresh_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None

def _public_refresh_message(status, saved_count=0, analyzed_count=0, metadata_ok=True):
    """Return visitor-friendly status copy; never expose provider/quota diagnostics."""
    if not metadata_ok:
        return "News updates are temporarily unavailable. Please check back shortly."
    if status == "running":
        return "Refreshing the latest business news…"
    if status == "failed":
        return "We couldn't update the news just now. Please try again shortly."
    if status == "completed":
        if saved_count > 0 and analyzed_count > 0:
            return f"Latest news updated: {saved_count} new articles added and {analyzed_count} article insights prepared."
        if saved_count > 0:
            return f"Latest news updated: {saved_count} new articles added. More article insights are being prepared."
        if analyzed_count > 0:
            return f"Latest news checked: {analyzed_count} article insights updated. No new articles were added this time."
        return "Latest news check complete. No new articles were added this time."
    return "The latest news feed is ready."


def get_news_refresh_state():
    metadata_ok = True
    try:
        data = _get_refresh_metadata()
    except Exception as error:
        app.logger.warning("Could not read news refresh status: %s", error)
        data = {}
        metadata_ok = False

    status = data.get("news_refresh_status", "idle")
    saved_count = int(data.get("news_refresh_saved_count", "0") or 0)
    analyzed_count = int(data.get("news_refresh_analyzed_count", "0") or 0)
    # Stored metadata may contain internal diagnostics from an earlier run.
    # Do not return that raw text from either the homepage or the status API.
    public_message = _public_refresh_message(
        status, saved_count=saved_count,
        analyzed_count=analyzed_count, metadata_ok=metadata_ok
    )
    return {
        "status": status if metadata_ok else "unavailable",
        "message": public_message,
        "last_success": data.get("news_refresh_last_success", ""),
        "last_finished": data.get("news_refresh_last_finished", ""),
        "candidate_count": int(data.get("news_refresh_candidate_count", "0") or 0),
        "saved_count": saved_count,
        "analyzed_count": analyzed_count,
    }

def _run_news_refresh_worker():
    started_at = _utc_now_text()
    candidate_count = 0
    saved_count = 0
    analyzed_count = 0
    note = ""
    try:
        _set_refresh_metadata(news_refresh_status="running", news_refresh_message="Fetching recent business news…", news_refresh_started_at=started_at)
        from config import NEWS_API_KEY
        if not NEWS_API_KEY:
            raise RuntimeError("NEWS_API_KEY is not configured in the hosting environment.")

        from refresh_pipeline import run_pipeline
        _set_refresh_metadata(news_refresh_message="Fetching recent business news and preparing AI analysis…")
        pipeline_result = run_pipeline()
        candidate_count = int(pipeline_result.get("candidate_count", 0) or 0)
        saved_count = int(pipeline_result.get("saved_count", 0) or 0)
        analyzed_count = int(pipeline_result.get("analyzed_count", 0) or 0)
        note = str(pipeline_result.get("note", "") or "")

        finished_at = _utc_now_text()
        message = f"News refresh complete: {saved_count} new articles added; {analyzed_count} analysed with AI." + note
        _set_refresh_metadata(
            news_refresh_status="completed", news_refresh_message=message,
            news_refresh_last_finished=finished_at, news_refresh_last_success=finished_at,
            news_refresh_started_at=finished_at,
            news_refresh_candidate_count=candidate_count, news_refresh_saved_count=saved_count,
            news_refresh_analyzed_count=analyzed_count
        )
    except BaseException as error:
        finished_at = _utc_now_text()
        error_text = str(error).lower()
        if "news_api_key" in error_text or "newsapi" in error_text or "provider quota" in error_text:
            safe_message = "The latest news could not be retrieved just now. Please try again shortly."
        elif "gemini" in error_text or "google.genai" in error_text or "generative language" in error_text:
            safe_message = "Some article insights are temporarily unavailable. Please check back shortly."
        elif "database" in error_text or "postgres" in error_text or "psycopg" in error_text or "connection" in error_text:
            safe_message = "The news feed is temporarily unavailable. Please try again shortly."
        elif "not configured" in error_text:
            safe_message = "The news update is temporarily unavailable. Please try again later."
        else:
            safe_message = "We couldn't complete the news update. Please try again shortly."
        app.logger.exception("News refresh failed")
        _set_refresh_metadata(
            news_refresh_status="failed",
            news_refresh_message=safe_message,
            news_refresh_last_finished=finished_at,
            news_refresh_started_at=finished_at,
            news_refresh_candidate_count=candidate_count,
            news_refresh_saved_count=saved_count,
            news_refresh_analyzed_count=analyzed_count
        )
    finally:
        if _NEWS_REFRESH_LOCK.locked():
            try:
                _NEWS_REFRESH_LOCK.release()
            except RuntimeError:
                pass

def start_news_refresh(force=False):
    """Launch one refresh, enforcing a six-hour interval for every trigger.

    ``force`` remains in the signature for compatibility with existing callers,
    but cannot bypass the shared interval.
    """
    if not _NEWS_REFRESH_LOCK.acquire(blocking=False):
        return False, "A news refresh is already running."
    try:
        meta = _get_refresh_metadata()
        last_attempt = _parse_refresh_time(meta.get("news_refresh_started_at"))
        now = datetime.now(timezone.utc)
        if meta.get("news_refresh_status") == "running":
            if last_attempt and (now - last_attempt).total_seconds() < 20 * 60:
                _NEWS_REFRESH_LOCK.release()
                return False, "A news refresh is already running."
            _set_refresh_metadata(news_refresh_status="failed", news_refresh_message="A previous refresh stopped before it completed. A new attempt will start now.", news_refresh_last_finished=_utc_now_text())
        # Apply the six-hour interval to manual, automatic and scheduled app
        # requests alike. Public response text stays generic and visitor-friendly.
        if last_attempt and (now - last_attempt).total_seconds() < NEWS_REFRESH_COOLDOWN_SECONDS:
            _NEWS_REFRESH_LOCK.release()
            return False, "The next news update is not due yet. Please check back later."
        _set_refresh_metadata(news_refresh_status="running", news_refresh_message="Starting news refresh…", news_refresh_started_at=_utc_now_text())
        worker = threading.Thread(target=_run_news_refresh_worker, name="BusinessNewsAgentNewsRefresh", daemon=True)
        worker.start()
        return True, "News refresh started."
    except Exception:
        if _NEWS_REFRESH_LOCK.locked():
            try:
                _NEWS_REFRESH_LOCK.release()
            except RuntimeError:
                pass
        raise

def maybe_auto_refresh_news():
    if not NEWS_AUTO_REFRESH:
        return
    try:
        state = _get_refresh_metadata()
        if state.get("news_refresh_status") == "running":
            return
        last_success = _parse_refresh_time(state.get("news_refresh_last_success"))
        now = datetime.now(timezone.utc)
        if last_success is None or (now - last_success).total_seconds() >= NEWS_REFRESH_INTERVAL_HOURS * 3600:
            start_news_refresh(force=False)
    except Exception as error:
        app.logger.warning("Automatic news refresh could not be started: %s", error)


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
        SELECT table_name AS name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ?
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
    try:
        if search:
            search_term = f"%{search}%"
            cursor.execute(
                """
                SELECT * FROM news
                WHERE headline ILIKE ? OR source ILIKE ? OR analysis ILIKE ?
                ORDER BY published_at DESC NULLS LAST, saved_at DESC
                LIMIT 100
                """,
                (search_term, search_term, search_term)
            )
        else:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
            cursor.execute(
                """
                SELECT * FROM news
                WHERE published_at >= ?
                ORDER BY published_at DESC NULLS LAST, saved_at DESC
                LIMIT 30
                """,
                (cutoff,)
            )
        return cursor.fetchall()
    finally:
        connection.close()

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
    if result["summary"].strip().lower() in {"pending ai analysis", "no analysis provided", "no analysis available"}:
        result["summary"] = ""
        return result

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
    cursor.execute("SELECT COUNT(*) FROM news")
    total_articles = cursor.fetchone()[0]

    if table_exists(connection, "predictions"):
        cursor.execute("SELECT COUNT(*) FROM predictions")
        total_predictions = cursor.fetchone()[0]
    else:
        total_predictions = 0

    total_comparisons = 0
    total_matches = 0
    validated_ids = set()

    if table_exists(connection, "predictions") and table_exists(connection, "market_results"):
        cursor.execute("""
            SELECT p.news_id, p.predicted_oil, p.predicted_nifty, p.predicted_energy,
                   m.oil_price, m.nifty_value, m.energy_average_change,
                   n.headline, n.analysis
            FROM predictions p
            INNER JOIN market_results m ON p.news_id = m.news_id
            INNER JOIN news n ON p.news_id = n.id
        """)
        rows = cursor.fetchall()
        for row in rows:
            # Exclude predictions that have not been matched to recorded actuals,
            # and exclude markets that the article was not relevant to.
            story_text = meaningful_article_text(row["headline"], row["analysis"])
            relevance = market_relevance(story_text)
            comparisons = [
                (normalize_prediction(row["predicted_oil"]), get_direction(row["oil_price"]), relevance["oil"]),
                (normalize_prediction(row["predicted_nifty"]), get_direction(row["nifty_value"]), relevance["nifty"]),
                (normalize_prediction(row["predicted_energy"]), get_direction(row["energy_average_change"]), relevance["energy"]),
            ]
            article_had_comparison = False
            for predicted, actual, is_relevant in comparisons:
                if not is_relevant or not predicted or actual == "NO DATA":
                    continue
                total_comparisons += 1
                article_had_comparison = True
                if predicted == actual:
                    total_matches += 1
            if article_had_comparison:
                validated_ids.add(row["news_id"])

    validated_articles = len(validated_ids)
    accuracy = (total_matches / total_comparisons * 100) if total_comparisons else 0
    connection.close()
    return {
        "total_articles": total_articles,
        "total_predictions": total_predictions,
        "validated_articles": validated_articles,
        "total_matches": total_matches,
        "total_comparisons": total_comparisons,
        "accuracy": round(accuracy, 1),
    }


# ============================================================
# SITE HELPERS AND COMMUNITY STORAGE
# ============================================================

# Set SECRET_KEY in the hosting environment. The DATABASE_URL-derived fallback
# keeps sessions consistent across Gunicorn workers when configuring the app.
_secret = os.getenv("SECRET_KEY", "").strip()
_db_secret = os.getenv("DATABASE_URL", "").strip()
app.secret_key = _secret or (
    hashlib.sha256((_db_secret + "|BusinessNewsAgent-session-key").encode()).hexdigest()
    if _db_secret else secrets.token_urlsafe(48)
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "true" if os.getenv("RENDER") else "false").lower() not in {"0", "false", "no"},
    MAX_CONTENT_LENGTH=64 * 1024,
)

COMMUNITY_ADMIN_PASSWORD = os.getenv("COMMUNITY_ADMIN_PASSWORD", "").strip()


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = get_csrf_token


def verify_csrf():
    provided = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not provided or not expected or not hmac.compare_digest(provided, expected):
        abort(400, description="Your form expired. Refresh the page and try again.")


def ensure_community_table():
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS community_posts (
                id BIGSERIAL PRIMARY KEY,
                display_name VARCHAR(80) NOT NULL,
                message TEXT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'hidden')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_community_posts_status_created ON community_posts (status, created_at DESC)")
        connection.commit()
    finally:
        connection.close()


def get_community_total_safe():
    try:
        ensure_community_table()
        connection = get_connection()
        try:
            row = connection.execute("SELECT COUNT(*) AS count FROM community_posts").fetchone()
            return int(row["count"] if row else 0)
        finally:
            connection.close()
    except Exception:
        # News pages should remain available even if the community table is
        # temporarily unavailable while the database is waking up.
        return 0


@app.context_processor
def inject_shared_context():
    return {
        "community_total": get_community_total_safe(),
        "community_admin_logged_in": bool(session.get("community_admin")),
    }


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("community_admin"):
            return redirect(url_for("community_admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# ============================================================
# MBA CONCEPTS: only concepts supported by article keywords
# ============================================================

MBA_CONCEPT_RULES = [
    ("Supply-chain risk", ["supply chain", "shipping", "freight", "port closure", "logistics", "shortage", "semiconductor", "chip supply", "trade route"], "A disruption or constraint in sourcing, production or delivery can raise costs and delay sales. Firms may diversify suppliers, hold strategic inventory or redesign routes to reduce exposure."),
    ("Inflation and cost pass-through", ["inflation", "input costs", "raw material costs", "oil prices", "crude oil", "fuel costs", "price hike", "price increase", "food prices"], "Higher input costs can squeeze margins. A company must decide how much of the increase customers will absorb and how much it must manage through efficiency or supplier negotiation."),
    ("Capital expenditure and capacity", ["capital expenditure", " capex", "new factory", "manufacturing plant", "factory in", "build a plant", "production facility", "manufacturing facility", "chip plant"], "Capital expenditure commits money today to build future capacity. Its value depends on demand, execution, financing cost and how quickly the investment generates cash flow."),
    ("Vertical integration", ["in-house", "vertically integrate", "vertical integration", "own chips", "make its own", "internal production", "bring production in-house"], "Vertical integration means controlling more stages of the value chain. It can improve control and resilience, but adds fixed costs and operating complexity."),
    ("Innovation and R&D", ["artificial intelligence", " ai ", "new technology", "research and development", "r&d", "patent", "generative ai", "automation", "robotics", "chip design"], "Innovation may create product advantages or productivity gains, but returns depend on adoption, execution, intellectual-property protection and competitive response."),
    ("Competitive strategy and differentiation", ["market share", "competitor", "competition", "rival", "product launch", "new model", "brand positioning", "differentiat", "price war"], "A firm competes through cost, differentiation or focus. The strategic question is whether the move creates a defensible customer benefit that rivals cannot easily copy."),
    ("Regulatory and policy risk", ["regulation", "regulator", "antitrust", "tariff", "sanction", "ban on", "court ruling", "compliance", "government policy", "export control", "subsidy"], "Policy changes can alter costs, market access or the rules of competition. Managers need to assess compliance, scenario-plan and adapt their operating model."),
    ("Exchange-rate exposure", ["exchange rate", "currency", "rupee", "dollar", "forex", "depreciat", "appreciat", "foreign exchange"], "Currency movements affect import costs, export competitiveness and translated earnings. Firms can mitigate exposure through pricing, natural hedges or financial hedging."),
    ("Demand and consumer behaviour", ["consumer demand", "customer demand", "sales volume", "consumer spending", "buyer demand", "retail sales", "demand slowdown", "demand surge", "orders fell", "orders rose"], "Demand reflects customer needs, income, expectations and alternatives. Companies may need to adjust price, inventory, promotion or capacity as demand changes."),
    ("Sustainability and ESG", ["sustainability", "sustainable", "carbon emissions", "net zero", "renewable energy", "climate target", "green energy", "decarbon", "electric vehicle", "ev adoption"], "Environmental and governance choices can affect costs, reputation, access to capital and long-term risk. Credible measurement matters because green claims without evidence can damage trust."),
    ("Economies of scale", ["scale up", "scaling production", "mass production", "production capacity", "capacity expansion", "high-volume", "volume production"], "Economies of scale occur when average cost falls as output increases. Expansion creates value only when demand can absorb output without eroding prices or quality."),
    ("Mergers and acquisition strategy", ["acquisition", "acquire", "merger", "takeover", "buyout", "stake in", "joint venture"], "An acquisition or alliance can add technology, customers or capabilities. The strategic test is whether synergies exceed the purchase price and integration risk."),
    ("Pricing power and margins", ["pricing power", "profit margin", "margin pressure", "raise prices", "price cuts", "discounting", "gross margin", "operating margin"], "Pricing power is the ability to raise prices without losing too many customers. It depends on differentiation, switching costs, competition and customer sensitivity."),
    ("Interest rates and cost of capital", ["interest rate", "rate cut", "rate hike", "central bank", "reserve bank", "monetary policy", "bond yield", "borrowing costs"], "Interest rates affect loan costs, consumer purchases and the present value of future cash flows. Capital-intensive businesses are often particularly sensitive to financing conditions."),
]


def build_mba_concepts(article_text, existing_key_concept=""):
    text = " " + re.sub(r"\s+", " ", (article_text or "").lower()) + " "
    matches = []
    for title, keywords, explanation in MBA_CONCEPT_RULES:
        found = [keyword for keyword in keywords if keyword.strip().lower() in text]
        if found:
            matches.append((len(found), title, explanation))
    matches.sort(key=lambda item: (-item[0], item[1]))
    concepts = [{"title": title, "explanation": explanation} for _, title, explanation in matches[:3]]
    if not concepts and existing_key_concept:
        clean = re.sub(r"\s+", " ", str(existing_key_concept)).strip()
        if clean and len(clean) <= 300 and clean.lower().strip(" .") not in {"no clear concept", "none", "not applicable", "n/a", "no clear mba concept"}:
            parts = re.split(r"\s*(?:—|–|:)\s*", clean, maxsplit=1)
            title = parts[0].strip().rstrip(".")
            explanation = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "This concept is identified in the article's AI analysis; the connection is shown only where the available article supports it."
            concepts.append({"title": title, "explanation": explanation})
    return concepts[:3]


# ============================================================
# MARKET RELEVANCE: avoid displaying unrelated predictions
# ============================================================

OIL_TERMS = ["crude oil", "oil price", "oil prices", "brent", "wti", "opec", "petroleum", "oil supply", "oil production", "barrel of oil", "refinery", "gasoline prices"]
INDIA_MACRO_TERMS = ["nifty", "sensex", "indian stock", "india's economy", "indian economy", "reserve bank of india", "rbi", "indian inflation", "india gdp", "indian rupee", "rupee", "india's gdp", "india trade deficit", "indian interest rate", "indian bond", "india tariff", "india exports", "india imports"]
GLOBAL_MACRO_TERMS = ["federal reserve", "fed rate", "interest rate", "inflation", "gdp growth", "recession", "central bank", "tariff", "trade war", "global economy", "bond yields", "economic growth"]
ENERGY_TERMS = ["energy sector", "energy stocks", "power sector", "electricity", "renewable energy", "solar power", "wind power", "natural gas", "oil and gas", "power plant", "energy company"]
INDIAN_ENERGY_COMPANIES = ["reliance industries", "ongc", "ntpc", "adani green", "adani power", "tata power", "coal india", "gail", "oil india", "indian oil", "ioc", "bpcl", "hpcl", "power grid"]


def meaningful_article_text(headline, analysis):
    """Use story content only, excluding generated prediction boilerplate."""
    story_analysis = re.split(
        r"(?:^|\n)\s*(?:\d+[\.)]\s*)?(?:MARKET PREDICTION|INDIA IMPACT SCORE)\s*:?",
        str(analysis or "").replace("**", ""),
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    parsed = parse_analysis(story_analysis)
    parts = [
        headline or "",
        parsed.get("summary", ""),
        parsed.get("global_impact", ""),
        parsed.get("india_impact", ""),
        parsed.get("sentiment_reason", ""),
        parsed.get("impact_reason", ""),
        parsed.get("key_concept", ""),
    ]
    return " ".join(str(part) for part in parts if part)


def market_relevance(article_text):
    text = (article_text or "").lower()
    def contains(term):
        if term.upper() in {"RBI", "NIFTY", "GDP", "WTI", "OPEC", "IOC", "BPCL", "HPCL", "NTPC", "ONGC"}:
            return bool(re.search(r"\b" + re.escape(term.lower()) + r"\b", text))
        return term in text
    has_oil = any(contains(term) for term in OIL_TERMS)
    has_india_macro = any(contains(term) for term in INDIA_MACRO_TERMS)
    has_global_macro = any(contains(term) for term in GLOBAL_MACRO_TERMS)
    has_indian_energy_company = any(contains(term) for term in INDIAN_ENERGY_COMPANIES)
    has_energy = any(contains(term) for term in ENERGY_TERMS) or has_indian_energy_company
    # NIFTY is shown only for Indian-market or broad macroeconomic stories.
    has_india_context = any(contains(term) for term in ["india", "indian", "rupee", "nifty", "sensex", "rbi"])
    return {
        "oil": has_oil,
        "nifty": has_india_macro or (has_global_macro and has_india_context),
        "energy": has_indian_energy_company or (has_energy and has_india_context),
    }


# Map only explicitly recognised companies. Symbols are allow-listed before
# being passed to the market-data endpoint.
COMPANY_MAP = [
    ("Tesla", "TSLA", "US"), ("Wipro", "WIPRO.NS", "IN"),
    ("Reliance Industries", "RELIANCE.NS", "IN"), ("Reliance", "RELIANCE.NS", "IN"),
    ("Tata Motors", "TATAMOTORS.NS", "IN"), ("Infosys", "INFY.NS", "IN"),
    ("TCS", "TCS.NS", "IN"), ("Tata Consultancy Services", "TCS.NS", "IN"),
    ("HDFC Bank", "HDFCBANK.NS", "IN"), ("ICICI Bank", "ICICIBANK.NS", "IN"),
    ("Adani Enterprises", "ADANIENT.NS", "IN"), ("Adani Green", "ADANIGREEN.NS", "IN"),
    ("ONGC", "ONGC.NS", "IN"), ("NTPC", "NTPC.NS", "IN"),
    ("Tata Steel", "TATASTEEL.NS", "IN"), ("Hindustan Unilever", "HINDUNILVR.NS", "IN"),
    ("Asian Paints", "ASIANPAINT.NS", "IN"), ("Tata Power", "TATAPOWER.NS", "IN"),
    ("Amazon", "AMZN", "US"), ("Apple", "AAPL", "US"),
    ("Microsoft", "MSFT", "US"), ("NVIDIA", "NVDA", "US"),
    ("Alphabet", "GOOGL", "US"), ("Google", "GOOGL", "US"), ("Meta", "META", "US"),
]
ALLOWED_SYMBOLS = {symbol for _, symbol, _ in COMPANY_MAP}
COMPANY_CACHE = {}
COMPANY_CACHE_LOCK = threading.Lock()
COMPANY_CACHE_SECONDS = 4 * 60 * 60


def detect_company(article_text):
    text = (article_text or "").lower()
    # Long aliases first to avoid matching "Reliance" ahead of its full name.
    for name, symbol, market in sorted(COMPANY_MAP, key=lambda item: len(item[0]), reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9])", text):
            return {"name": name, "symbol": symbol, "market": market}
    return None


def format_financial_number(value, currency=""):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number or number != number:
        return None
    absolute = abs(number)
    if absolute >= 1_000_000_000_000:
        value_text = f"{number / 1_000_000_000_000:.2f}T"
    elif absolute >= 1_000_000_000:
        value_text = f"{number / 1_000_000_000:.2f}B"
    elif absolute >= 1_000_000:
        value_text = f"{number / 1_000_000:.2f}M"
    else:
        value_text = f"{number:,.2f}"
    return f"{currency} {value_text}".strip()


def fetch_company_profile(symbol):
    if symbol not in ALLOWED_SYMBOLS:
        return None, 400
    now = datetime.utcnow()
    with COMPANY_CACHE_LOCK:
        cached = COMPANY_CACHE.get(symbol)
        if cached and (now - cached["cached_at"]).total_seconds() < COMPANY_CACHE_SECONDS:
            return cached["data"], 200
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.get_info() or {}
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        previous = info.get("regularMarketPreviousClose") or info.get("previousClose")
        currency = info.get("currency") or ("INR" if symbol.endswith(".NS") else "USD")
        trend = "flat"
        if price is not None and previous is not None:
            try:
                trend = "up" if float(price) > float(previous) else ("down" if float(price) < float(previous) else "flat")
            except (ValueError, TypeError):
                trend = "flat"
        market_time = info.get("regularMarketTime")
        market_timestamp = None
        if market_time:
            try:
                from datetime import timezone
                market_timestamp = datetime.fromtimestamp(int(market_time), timezone.utc).isoformat(timespec="minutes")
            except (ValueError, TypeError, OSError):
                market_timestamp = None
        profile = {
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName") or symbol,
            "exchange": info.get("fullExchangeName") or info.get("exchange") or ("NSE" if symbol.endswith(".NS") else "US market"),
            "currency": currency,
            "price": price,
            "previous_close": previous,
            "change_percent": None,
            "trend": trend,
            "revenue": format_financial_number(info.get("totalRevenue"), currency),
            "net_income": format_financial_number(info.get("netIncomeToCommon"), currency),
            "parent": info.get("parentCompany") or "Not available in the current market-data feed",
            "subsidiaries": info.get("subsidiaries") or [],
            "market_cap": format_financial_number(info.get("marketCap"), currency),
            "source": "Yahoo Finance; quotes may be delayed",
            "market_timestamp": market_timestamp,
            "retrieved_at": datetime.utcnow().isoformat(timespec="minutes") + "Z",
        }
        if price is not None and previous not in (None, 0):
            try:
                profile["change_percent"] = round((float(price) - float(previous)) / float(previous) * 100, 2)
            except (ValueError, TypeError, ZeroDivisionError):
                pass
        if isinstance(profile["subsidiaries"], str):
            profile["subsidiaries"] = [v.strip() for v in profile["subsidiaries"].split(",") if v.strip()]
        profile["subsidiaries"] = profile["subsidiaries"][:8] if profile["subsidiaries"] else []
        with COMPANY_CACHE_LOCK:
            COMPANY_CACHE[symbol] = {"data": profile, "cached_at": now}
        return profile, 200
    except Exception as exc:
        # Do not leak provider error details to visitors.
        return {"symbol": symbol, "error": "Company data is temporarily unavailable. Please try again later.", "source": "Yahoo Finance; quotes may be delayed"}, 200


@app.route("/api/company/<symbol>")
def company_api(symbol):
    if symbol not in ALLOWED_SYMBOLS:
        abort(404)
    profile, status = fetch_company_profile(symbol)
    response = jsonify(profile or {"error": "Company data unavailable"})
    response.status_code = status
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def index():
    maybe_auto_refresh_news()
    search = request.args.get("search", "").strip()[:160]
    rows = get_news(search)
    refresh_state = get_news_refresh_state()
    articles = []
    for row in rows:
        article = dict(row)
        article["analysis_data"] = parse_analysis(article.get("analysis", ""))
        article_text = meaningful_article_text(article.get("headline", ""), article.get("analysis", ""))
        article_text = " ".join([article_text, str(article.get("description") or ""), str(article.get("content") or "")]).strip()
        article["mba_concepts"] = build_mba_concepts(article_text, article["analysis_data"].get("key_concept", ""))
        article["company"] = detect_company(str(article.get("headline") or "") + " " + article_text)
        relevance = market_relevance(article_text)
        article["market_relevance"] = relevance
        prediction = get_ai_prediction(article["id"])
        # Strip predictions for markets that this story does not materially relate to.
        if not relevance["oil"]:
            prediction["oil"] = ""
        if not relevance["nifty"]:
            prediction["nifty"] = ""
        if not relevance["energy"]:
            prediction["energy"] = ""
        article["prediction_data"] = prediction
        article["show_predictions"] = any(relevance.values()) and any([prediction.get("oil"), prediction.get("nifty"), prediction.get("energy")])
        article["comparison"] = get_market_comparison(article["id"]) if article["show_predictions"] else empty_comparison()
        comparison = article["comparison"]
        eligible_results = []
        for scope, result_key in [("oil", "oil_result"), ("nifty", "nifty_result"), ("energy", "energy_result")]:
            result = comparison.get(result_key, "NO DATA")
            if relevance[scope] and result in {"MATCH", "MISMATCH"}:
                eligible_results.append(result)
        comparison["matches"] = sum(result == "MATCH" for result in eligible_results)
        comparison["comparable"] = len(eligible_results)
        articles.append(article)
    global_stats = get_global_stats()
    return render_template(
        "index.html", articles=articles, search=search,
        total_articles=len(articles), global_stats=global_stats, refresh_state=refresh_state,
        current_tab="home", page_title="Business & Economic Intelligence"
    )


@app.route("/refresh-news", methods=["POST"])
def refresh_news_route():
    try:
        started, message = start_news_refresh(force=True)
        state = get_news_refresh_state()
        status_code = 202 if started else (409 if state["status"] == "running" else 429)
        return jsonify({"started": started, "message": message, "state": state}), status_code
    except Exception as error:
        app.logger.exception("Could not start news refresh")
        return jsonify({"started": False, "message": "We could not start the news update. Please try again shortly."}), 500


@app.route("/refresh-news/status", methods=["GET"])
def refresh_news_status_route():
    return jsonify(get_news_refresh_state())


# ============================================================
# INDIA MARKET OUTLOOK
# ============================================================

def vote_summary(votes):
    total = sum(votes.values())
    if total == 0:
        return {"direction": "No eligible data", "total": 0, "votes": votes, "confidence": 0}
    ordered = sorted(votes.items(), key=lambda item: (-item[1], ["UP", "DOWN", "FLAT"].index(item[0])))
    direction, count = ordered[0]
    return {"direction": direction, "total": total, "votes": votes, "confidence": round(count / total * 100)}


@app.route("/india-market-outlook")
def india_market_outlook():
    cutoff = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    votes = {"oil": {"UP": 0, "DOWN": 0, "FLAT": 0}, "nifty": {"UP": 0, "DOWN": 0, "FLAT": 0}, "energy": {"UP": 0, "DOWN": 0, "FLAT": 0}}
    recent_items = []
    try:
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("""
                SELECT p.predicted_oil, p.predicted_nifty, p.predicted_energy,
                       p.prediction_date, n.headline, n.analysis, n.source
                FROM predictions p
                INNER JOIN news n ON n.id = p.news_id
                WHERE p.prediction_date >= ?
                ORDER BY p.prediction_date DESC
                LIMIT 200
            """, (cutoff,))
            recent_items = [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()
    except Exception:
        recent_items = []
    eligible = 0
    for item in recent_items:
        article_text = meaningful_article_text(item.get("headline", ""), item.get("analysis", ""))
        relevance = market_relevance(article_text)
        row_had_eligible = False
        for key, column in [("oil", "predicted_oil"), ("nifty", "predicted_nifty"), ("energy", "predicted_energy")]:
            if not relevance[key]:
                continue
            direction = normalize_prediction(item.get(column))
            if direction in votes[key]:
                votes[key][direction] += 1
                row_had_eligible = True
        eligible += 1 if row_had_eligible else 0
    outlook = {key: vote_summary(value) for key, value in votes.items()}
    return render_template("india_market_outlook.html", current_tab="outlook", page_title="India Market Outlook", outlook=outlook, eligible_articles=eligible, days=3)


# ============================================================
# COMMUNITY: pending-by-default, daily random approved selection
# ============================================================

@app.route("/community")
def community():
    ensure_community_table()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT id, display_name, message, created_at FROM community_posts WHERE status = 'approved' ORDER BY created_at DESC LIMIT 500")
        approved = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT COUNT(*) AS count FROM community_posts WHERE status = 'pending'")
        pending_count = int(cursor.fetchone()["count"])
    finally:
        connection.close()
    # Same curated random sample during a calendar day, different sample on a new day.
    seed = int(hashlib.sha256(date.today().isoformat().encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    featured = rng.sample(approved, min(3, len(approved))) if approved else []
    counts = {"all": get_community_total_safe(), "approved": len(approved), "pending": pending_count}
    return render_template("community.html", current_tab="community", page_title="Community", featured=featured, counts=counts)


@app.route("/community/submit", methods=["POST"])
def community_submit():
    verify_csrf()
    # Honeypot field: bots often populate hidden fields.
    if request.form.get("website", "").strip():
        return redirect(url_for("community"))
    now = datetime.utcnow().timestamp()
    last_post = float(session.get("last_community_post", 0))
    if now - last_post < 45:
        flash("Please wait a little before submitting another post.", "error")
        return redirect(url_for("community"))
    name = re.sub(r"\s+", " ", request.form.get("display_name", "").strip())[:80]
    message = re.sub(r"\s+", " ", request.form.get("message", "").strip())[:1000]
    if not name or not message or len(message) < 8:
        flash("Please enter a name and a post of at least 8 characters.", "error")
        return redirect(url_for("community"))
    ensure_community_table()
    connection = get_connection()
    try:
        connection.execute("INSERT INTO community_posts (display_name, message, status) VALUES (?, ?, 'pending')", (name, message))
        connection.commit()
    finally:
        connection.close()
    session["last_community_post"] = now
    flash("Thank you! Your post has been received and will appear after owner approval.", "success")
    return redirect(url_for("community"))


@app.route("/admin/community/login", methods=["GET", "POST"])
def community_admin_login():
    if request.method == "POST":
        verify_csrf()
        configured = os.getenv("COMMUNITY_ADMIN_PASSWORD", "").strip()
        submitted = request.form.get("password", "")
        if not configured:
            flash("Owner access is not configured yet. Set COMMUNITY_ADMIN_PASSWORD in the hosting environment variables first.", "error")
        elif hmac.compare_digest(submitted, configured):
            session["community_admin"] = True
            session.permanent = True
            flash("Owner access granted.", "success")
            next_url = request.args.get("next", "")
            if next_url.startswith("/admin/community"):
                return redirect(next_url)
            return redirect(url_for("community_admin"))
        else:
            flash("Incorrect password.", "error")
    return render_template("community_admin_login.html", current_tab="community", page_title="Owner sign in", password_configured=bool(os.getenv("COMMUNITY_ADMIN_PASSWORD", "").strip()))


@app.route("/admin/community/logout", methods=["POST"])
@admin_required
def community_admin_logout():
    verify_csrf()
    session.pop("community_admin", None)
    flash("You have signed out of owner moderation.", "success")
    return redirect(url_for("community"))


@app.route("/admin/community")
@admin_required
def community_admin():
    ensure_community_table()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT id, display_name, message, status, created_at FROM community_posts ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, created_at DESC LIMIT 300")
        posts = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT status, COUNT(*) AS count FROM community_posts GROUP BY status")
        status_counts = {row["status"]: int(row["count"]) for row in cursor.fetchall()}
    finally:
        connection.close()
    return render_template("community_admin.html", current_tab="community", page_title="Moderate community", posts=posts, status_counts=status_counts, all_count=get_community_total_safe())


@app.route("/admin/community/<int:post_id>/status", methods=["POST"])
@admin_required
def community_post_status(post_id):
    verify_csrf()
    status = request.form.get("status", "")
    if status not in {"pending", "approved", "hidden"}:
        abort(400, description="Invalid community post status.")
    ensure_community_table()
    connection = get_connection()
    try:
        connection.execute("UPDATE community_posts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, post_id))
        connection.commit()
    finally:
        connection.close()
    flash("Post status updated.", "success")
    return redirect(url_for("community_admin"))


@app.route("/admin/community/<int:post_id>/delete", methods=["POST"])
@admin_required
def community_post_delete(post_id):
    verify_csrf()
    ensure_community_table()
    connection = get_connection()
    try:
        connection.execute("DELETE FROM community_posts WHERE id = ?", (post_id,))
        connection.commit()
    finally:
        connection.close()
    flash("Post permanently deleted.", "success")
    return redirect(url_for("community_admin"))


# ============================================================
# ABOUT AND SUPPORT
# ============================================================

@app.route("/about")
def about():
    return render_template("about.html", current_tab="about", page_title="About the author", author_name=os.getenv("AUTHOR_NAME", "Neelam Saikia"))


@app.route("/support")
def support():
    support_link = os.getenv("SUPPORT_LINK", "").strip()
    if support_link and not re.match(r"^https://", support_link, flags=re.IGNORECASE):
        support_link = ""
    return render_template("support.html", current_tab="support", page_title="Support this project", support_link=support_link, github_url="https://github.com/NeelamSaikia25/BusinessNewsAgent")


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():
    return {"status": "running", "service": "BusinessNewsAgent"}


# ============================================================
# RUN LOCALLY
# ============================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
