"""One shared news ingestion + recent AI analysis pipeline.

Used by the Flask background refresh and the scheduled GitHub Actions workflow.
Credentials must be supplied through environment variables, never source code.
"""

from config import NEWS_API_KEY, GEMINI_API_KEY


REFRESH_INTERVAL_SECONDS = 6 * 60 * 60


def _is_github_actions():
    import os
    return os.getenv("GITHUB_ACTIONS", "").lower() == "true"


def _parse_utc(value):
    from datetime import datetime, timezone
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _set_metadata(connection, values):
    for key, value in values.items():
        connection.execute(
            "INSERT INTO app_metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
            (key, str(value if value is not None else "")),
        )


def _claim_scheduled_refresh_window():
    """Share the six-hour interval between Render and scheduled GitHub runs.

    The Flask worker manages its own start timestamp. GitHub Actions must check
    the same Neon metadata so a scheduled run cannot duplicate a recent web run.
    """
    if not _is_github_actions():
        return True

    from datetime import datetime, timezone
    import db_compat as sqlite3

    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_text = now.isoformat().replace("+00:00", "Z")
    connection = sqlite3.connect()
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        cursor = connection.cursor()
        cursor.execute(
            "SELECT key, value FROM app_metadata WHERE key IN (?, ?, ?)",
            ("news_refresh_started_at", "news_refresh_status", "news_refresh_last_success"),
        )
        metadata = {row[0]: row[1] for row in cursor.fetchall()}
        last_attempt = _parse_utc(metadata.get("news_refresh_started_at"))
        elapsed = (now - last_attempt).total_seconds() if last_attempt else None

        if metadata.get("news_refresh_status") == "running" and elapsed is not None and elapsed < 20 * 60:
            print("Scheduled update skipped because a recent update is already running.")
            return False
        if elapsed is not None and elapsed < REFRESH_INTERVAL_SECONDS:
            print("Scheduled update skipped; the next update window has not arrived yet.")
            return False

        _set_metadata(connection, {
            "news_refresh_status": "running",
            "news_refresh_message": "News update in progress.",
            "news_refresh_started_at": now_text,
        })
        connection.commit()
        return True
    finally:
        connection.close()


def _record_github_actions_success(outcome):
    """Persist the result so the website can show fresh status safely."""
    if not _is_github_actions():
        return
    from datetime import datetime, timezone
    import db_compat as sqlite3
    finished = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    connection = sqlite3.connect()
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        updates = {
            "news_refresh_status": "completed",
            "news_refresh_message": f"Scheduled news update complete: {outcome.get('saved_count', 0)} articles added.",
            "news_refresh_last_finished": finished,
            "news_refresh_last_success": finished,
            "news_refresh_started_at": finished,
            "news_refresh_candidate_count": outcome.get("candidate_count", 0),
            "news_refresh_saved_count": outcome.get("saved_count", 0),
            "news_refresh_analyzed_count": outcome.get("analyzed_count", 0),
        }
        _set_metadata(connection, updates)
        connection.commit()
    finally:
        connection.close()


def _record_github_actions_failure():
    """Persist a generic status and a finish-time cooldown after a failed run."""
    if not _is_github_actions():
        return
    from datetime import datetime, timezone
    import db_compat as sqlite3
    finished = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    connection = sqlite3.connect()
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        _set_metadata(connection, {
            "news_refresh_status": "failed",
            "news_refresh_message": "The news update could not be completed.",
            "news_refresh_last_finished": finished,
            "news_refresh_started_at": finished,
        })
        connection.commit()
    finally:
        connection.close()


def run_pipeline():
    if not _claim_scheduled_refresh_window():
        return {
            "status": "skipped",
            "candidate_count": 0,
            "selected_count": 0,
            "saved_count": 0,
            "analyzed_count": 0,
            "note": "",
        }

    try:
        if not NEWS_API_KEY:
            raise RuntimeError("NEWS_API_KEY is not configured.")

        import news
        fetch_result = news.main() or {}
        candidate_count = int(fetch_result.get("candidate_count", 0) or 0)
        if candidate_count == 0:
            raise RuntimeError("NewsAPI returned no articles. Check the configured provider and logs.")

        result = {
            "status": str(fetch_result.get("status", "completed")),
            "candidate_count": candidate_count,
            "selected_count": int(fetch_result.get("selected_count", 0) or 0),
            "saved_count": int(fetch_result.get("saved_count", 0) or 0),
            "analyzed_count": 0,
            "note": "",
        }

        if not GEMINI_API_KEY:
            result["note"] = ""
            _record_github_actions_success(result)
            return result

        import summarizer
        summarizer.create_predictions_table()
        stats = summarizer.process_recent_pending_news(hours=72, max_articles=10)
        result["analyzed_count"] = int(stats.get("processed", 0) or 0)
        # Any quota/service detail remains in server logs and is never used as
        # visitor-facing copy; the web app maps status to generic text.
        _record_github_actions_success(result)
        return result
    except Exception:
        _record_github_actions_failure()
        raise


if __name__ == "__main__":
    outcome = run_pipeline()
    print("\nBUSINESSNEWSAGENT REFRESH RESULT")
    for key, value in outcome.items():
        print(f"{key}: {value}")
