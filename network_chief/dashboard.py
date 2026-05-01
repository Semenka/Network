"""Network Chief performance dashboard.

Computes a set of KPIs grounded in the practices of well-known network
and community builders (Hoffman, Ferrazzi, Grant, Spinks/CMX), persists
each run as a ``kpi_snapshots`` row, and renders a concise markdown
summary with deltas vs the previous snapshot of the same window.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .db import new_id, now_iso, rows_to_dicts


def _isofmt(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _window_floor(window_days: int) -> str:
    return _isofmt(datetime.now(UTC) - timedelta(days=window_days))


def _stale_floor(days: int = 90) -> str:
    return _isofmt(datetime.now(UTC) - timedelta(days=days))


def _scalar(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = con.execute(sql, params).fetchone()
    if row is None:
        return 0
    value = row[0]
    return int(value) if value is not None else 0


def _percent(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


def compute_dashboard(con: sqlite3.Connection, *, window_days: int = 30) -> dict[str, Any]:
    """Compute the full KPI snapshot for ``window_days`` (default 30)."""
    floor = _window_floor(window_days)
    stale_90 = _stale_floor(90)
    stale_30 = _stale_floor(30)
    stale_7 = _stale_floor(7)

    total_people = _scalar(con, "SELECT count(*) FROM people")
    new_people_in_window = _scalar(
        con, "SELECT count(*) FROM people WHERE created_at >= ?", (floor,)
    )
    pct_with_email = _percent(
        _scalar(con, "SELECT count(*) FROM people WHERE primary_email IS NOT NULL AND primary_email != ''"),
        total_people,
    )
    pct_with_linkedin = _percent(
        _scalar(con, "SELECT count(*) FROM people WHERE linkedin_url IS NOT NULL AND linkedin_url != ''"),
        total_people,
    )
    pct_with_twitter = _percent(
        _scalar(con, "SELECT count(*) FROM people WHERE twitter_handle IS NOT NULL AND twitter_handle != ''"),
        total_people,
    )
    pct_with_phone = _percent(
        _scalar(con, "SELECT count(*) FROM people WHERE phone IS NOT NULL AND phone != ''"),
        total_people,
    )

    sources_rows = con.execute(
        """
        SELECT source, count(DISTINCT person_id) AS n
          FROM source_facts
         WHERE source IS NOT NULL
         GROUP BY source
         ORDER BY n DESC
        """
    ).fetchall()
    sources_breakdown = [{"source": row["source"], "people": int(row["n"])} for row in sources_rows]

    touches_7 = _scalar(con, "SELECT count(*) FROM interactions WHERE occurred_at >= ?", (stale_7,))
    touches_30 = _scalar(con, "SELECT count(*) FROM interactions WHERE occurred_at >= ?", (stale_30,))
    touches_90 = _scalar(con, "SELECT count(*) FROM interactions WHERE occurred_at >= ?", (stale_90,))
    active_30 = _scalar(
        con,
        "SELECT count(DISTINCT person_id) FROM interactions WHERE occurred_at >= ?",
        (stale_30,),
    )
    active_90 = _scalar(
        con,
        "SELECT count(DISTINCT person_id) FROM interactions WHERE occurred_at >= ?",
        (stale_90,),
    )
    pct_active_30 = _percent(active_30, total_people)
    pct_active_90 = _percent(active_90, total_people)

    incoming = _scalar(
        con, "SELECT count(*) FROM interactions WHERE direction = 'incoming' AND occurred_at >= ?", (stale_90,)
    )
    outgoing = _scalar(
        con, "SELECT count(*) FROM interactions WHERE direction = 'outgoing' AND occurred_at >= ?", (stale_90,)
    )
    inbound_ratio = round(incoming / outgoing, 2) if outgoing else None

    stale_high_value = _scalar(
        con,
        """
        SELECT count(DISTINCT cv.person_id)
          FROM connection_values cv
          LEFT JOIN relationships rel ON rel.person_id = cv.person_id
         WHERE cv.score >= 60
           AND (rel.last_interaction_at IS NULL OR rel.last_interaction_at < ?)
        """,
        (stale_90,),
    )

    drafts_created = _scalar(
        con, "SELECT count(*) FROM drafts WHERE created_at >= ?", (floor,)
    )
    drafts_approved = _scalar(
        con, "SELECT count(*) FROM drafts WHERE status = 'approved' AND updated_at >= ?", (floor,)
    )
    drafts_rejected = _scalar(
        con, "SELECT count(*) FROM drafts WHERE status = 'rejected' AND updated_at >= ?", (floor,)
    )
    decided = drafts_approved + drafts_rejected
    approval_rate = _percent(drafts_approved, decided)

    decision_latency_row = con.execute(
        """
        SELECT avg((julianday(updated_at) - julianday(created_at)) * 24)
          FROM drafts
         WHERE status IN ('approved', 'rejected')
           AND updated_at >= ?
        """,
        (floor,),
    ).fetchone()
    mean_decision_hours = (
        round(float(decision_latency_row[0]), 2)
        if decision_latency_row and decision_latency_row[0] is not None
        else None
    )

    drafts_by_channel = rows_to_dicts(
        con.execute(
            """
            SELECT channel, count(*) AS n
              FROM drafts
             WHERE created_at >= ?
             GROUP BY channel
             ORDER BY n DESC
            """,
            (floor,),
        ).fetchall()
    )

    sync_rows = rows_to_dicts(
        con.execute(
            """
            SELECT source, status, count(*) AS runs, max(finished_at) AS last_run
              FROM source_runs
             WHERE finished_at >= ?
             GROUP BY source, status
             ORDER BY last_run DESC
            """,
            (floor,),
        ).fetchall()
    )

    value_coverage_rows = con.execute(
        """
        SELECT value_type, count(DISTINCT person_id) AS n
          FROM connection_values
         WHERE score >= 60
         GROUP BY value_type
        """
    ).fetchall()
    value_coverage = {row["value_type"]: int(row["n"]) for row in value_coverage_rows}
    median_value_score_row = con.execute(
        """
        SELECT score
          FROM connection_values
         ORDER BY score
         LIMIT 1
        OFFSET (SELECT count(*) FROM connection_values) / 2
        """
    ).fetchone()
    median_value_score = int(median_value_score_row[0]) if median_value_score_row else 0

    goals_rows = rows_to_dicts(con.execute("SELECT * FROM goals WHERE status = 'active'").fetchall())
    goal_coverage: list[dict[str, Any]] = []
    for goal in goals_rows:
        segment = (goal.get("target_segment") or "").lower()
        capital = (goal.get("capital_type") or "").lower()
        if not segment and not capital:
            count = total_people
        else:
            sql = """
                SELECT count(DISTINCT p.id)
                  FROM people p
                  LEFT JOIN connection_values cv ON cv.person_id = p.id
                  LEFT JOIN roles r ON r.person_id = p.id
                  LEFT JOIN organizations o ON o.id = r.organization_id
                 WHERE 1 = 1
            """
            params: list[Any] = []
            if segment:
                sql += (
                    " AND ("
                    "lower(p.full_name) LIKE ? OR lower(p.notes) LIKE ? OR lower(r.title) LIKE ? "
                    "OR lower(o.name) LIKE ? OR lower(o.sector) LIKE ?"
                    ")"
                )
                like = f"%{segment}%"
                params.extend([like, like, like, like, like])
            if capital:
                sql += " AND lower(cv.value_type) LIKE ?"
                params.append(f"%{capital}%")
            count = _scalar(con, sql, tuple(params))
        goal_coverage.append(
            {
                "id": goal["id"],
                "title": goal["title"],
                "cadence": goal["cadence"],
                "matching_people": count,
            }
        )

    return {
        "captured_at": now_iso(),
        "window_days": window_days,
        "breadth": {
            "total_people": total_people,
            "new_in_window": new_people_in_window,
            "sources": sources_breakdown,
            "identity_coverage_pct": {
                "email": pct_with_email,
                "linkedin": pct_with_linkedin,
                "twitter": pct_with_twitter,
                "phone": pct_with_phone,
            },
        },
        "cadence": {
            "touches_7d": touches_7,
            "touches_30d": touches_30,
            "touches_90d": touches_90,
            "active_people_30d": active_30,
            "active_people_90d": active_90,
            "pct_active_30d": pct_active_30,
            "pct_active_90d": pct_active_90,
            "incoming_90d": incoming,
            "outgoing_90d": outgoing,
            "inbound_outbound_ratio": inbound_ratio,
            "stale_high_value": stale_high_value,
        },
        "pipeline": {
            "drafts_created": drafts_created,
            "drafts_approved": drafts_approved,
            "drafts_rejected": drafts_rejected,
            "approval_rate_pct": approval_rate,
            "mean_decision_hours": mean_decision_hours,
            "drafts_by_channel": drafts_by_channel,
            "sync_runs": sync_rows,
        },
        "value": {
            "by_type_score_60_plus": value_coverage,
            "median_value_score": median_value_score,
        },
        "goals": goal_coverage,
    }


def save_snapshot(con: sqlite3.Connection, snapshot: dict[str, Any]) -> str:
    snapshot_id = new_id()
    con.execute(
        """
        INSERT INTO kpi_snapshots (id, captured_at, window_days, metrics_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            snapshot_id,
            snapshot["captured_at"],
            int(snapshot["window_days"]),
            json.dumps(snapshot, sort_keys=True),
        ),
    )
    con.commit()
    return snapshot_id


def previous_snapshot(con: sqlite3.Connection, *, window_days: int, before: str | None = None) -> dict[str, Any] | None:
    if before is None:
        row = con.execute(
            "SELECT metrics_json FROM kpi_snapshots WHERE window_days = ? ORDER BY captured_at DESC LIMIT 1",
            (window_days,),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT metrics_json FROM kpi_snapshots WHERE window_days = ? AND captured_at < ? ORDER BY captured_at DESC LIMIT 1",
            (window_days, before),
        ).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def _delta(current: int | float | None, prev: int | float | None) -> str:
    if prev is None or current is None:
        return ""
    diff = current - prev
    if diff == 0:
        return " (=)"
    sign = "+" if diff > 0 else ""
    if isinstance(diff, float):
        return f" ({sign}{diff:.1f})"
    return f" ({sign}{diff})"


def _get(d: dict[str, Any] | None, *path: str) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def render_markdown(snapshot: dict[str, Any], previous: dict[str, Any] | None = None) -> str:
    out: list[str] = []
    captured = snapshot["captured_at"]
    window = snapshot["window_days"]
    out.append(f"# Network Chief Dashboard ({window}-day window)")
    out.append(f"_Captured at {captured}{' • compared to ' + previous['captured_at'] if previous else ' • first snapshot'}_")
    out.append("")

    breadth = snapshot["breadth"]
    prev_breadth = _get(previous, "breadth") or {}
    out.append("## 1. Network breadth")
    out.append(
        f"- **Total contacts**: {breadth['total_people']}{_delta(breadth['total_people'], prev_breadth.get('total_people'))}"
    )
    out.append(f"- **New in last {window}d**: {breadth['new_in_window']}")
    cov = breadth["identity_coverage_pct"]
    prev_cov = prev_breadth.get("identity_coverage_pct") or {}
    out.append(
        "- **Identity coverage**: "
        f"email {cov['email']}%{_delta(cov['email'], prev_cov.get('email'))} • "
        f"linkedin {cov['linkedin']}%{_delta(cov['linkedin'], prev_cov.get('linkedin'))} • "
        f"twitter {cov['twitter']}% • phone {cov['phone']}%"
    )
    if breadth["sources"]:
        out.append("- **Source contribution** (people with at least one fact from each source):")
        for entry in breadth["sources"][:8]:
            out.append(f"  - `{entry['source']}` → {entry['people']}")
    out.append("")

    cadence = snapshot["cadence"]
    prev_cadence = _get(previous, "cadence") or {}
    out.append("## 2. Engagement cadence")
    out.append(
        f"- **Touches**: 7d {cadence['touches_7d']}{_delta(cadence['touches_7d'], prev_cadence.get('touches_7d'))} • "
        f"30d {cadence['touches_30d']}{_delta(cadence['touches_30d'], prev_cadence.get('touches_30d'))} • "
        f"90d {cadence['touches_90d']}{_delta(cadence['touches_90d'], prev_cadence.get('touches_90d'))}"
    )
    out.append(
        f"- **Active people (≥1 touch)**: 30d {cadence['active_people_30d']} ({cadence['pct_active_30d']}%) • "
        f"90d {cadence['active_people_90d']} ({cadence['pct_active_90d']}%)"
    )
    ratio = cadence["inbound_outbound_ratio"]
    ratio_str = "n/a" if ratio is None else f"{ratio} (incoming÷outgoing)"
    out.append(
        f"- **Reciprocity (90d)**: incoming {cadence['incoming_90d']} • outgoing {cadence['outgoing_90d']} → ratio {ratio_str}"
    )
    out.append(
        f"- **Stale-but-valuable** (value ≥60 AND no touch in 90d): "
        f"**{cadence['stale_high_value']}**{_delta(cadence['stale_high_value'], prev_cadence.get('stale_high_value'))}"
    )
    out.append("")

    pipeline = snapshot["pipeline"]
    prev_pipeline = _get(previous, "pipeline") or {}
    out.append(f"## 3. Action pipeline (last {window}d)")
    out.append(
        f"- **Drafts**: created {pipeline['drafts_created']} • "
        f"approved {pipeline['drafts_approved']} • rejected {pipeline['drafts_rejected']}"
    )
    out.append(
        f"- **Approval rate**: {pipeline['approval_rate_pct']}%"
        f"{_delta(pipeline['approval_rate_pct'], prev_pipeline.get('approval_rate_pct'))}"
    )
    if pipeline["mean_decision_hours"] is not None:
        out.append(f"- **Mean time to draft decision**: {pipeline['mean_decision_hours']}h")
    if pipeline["drafts_by_channel"]:
        chans = ", ".join(f"{r['channel']}: {r['n']}" for r in pipeline["drafts_by_channel"])
        out.append(f"- **By channel**: {chans}")
    if pipeline["sync_runs"]:
        out.append("- **Sync runs**:")
        for run in pipeline["sync_runs"][:8]:
            out.append(f"  - `{run['source']}` ({run['status']}) ×{run['runs']} — last {run['last_run']}")
    out.append("")

    value = snapshot["value"]
    out.append("## 4. Value coverage")
    by_type = value["by_type_score_60_plus"] or {}
    if by_type:
        for vt in ("financial_capital", "competence", "specific_knowledge", "time_saving"):
            count = by_type.get(vt, 0)
            prev_count = ((_get(previous, "value", "by_type_score_60_plus") or {}).get(vt))
            out.append(f"- **{vt}** (score ≥60): {count}{_delta(count, prev_count)}")
    else:
        out.append("- No connection-value signals scored ≥60 yet. Run `network-chief maintain-values`.")
    out.append(f"- **Median value score**: {value['median_value_score']}")
    out.append("")

    goals = snapshot["goals"]
    out.append("## 5. Goal coverage")
    if not goals:
        out.append("- No active goals. Add one with `network-chief add-goal`.")
    else:
        for goal in goals:
            out.append(
                f"- _{goal['title']}_ ({goal['cadence']}): {goal['matching_people']} matching contacts"
            )
    out.append("")

    out.append("## 6. How to read this")
    out.append(
        "- **Network breadth** answers Reid Hoffman's *I+1/I+2* question: are you adding new nodes, "
        "and do you have enough reach surface (channels) to reach them?"
    )
    out.append(
        "- **Cadence** is Keith Ferrazzi's *3-touch rule* and CMX's *active vs lurking* split — "
        "if `pct_active_30d` is below ~10%, the network is going cold."
    )
    out.append(
        "- **Reciprocity** is Adam Grant's *givers vs takers* signal. Healthy operators run incoming÷outgoing "
        "around 0.7–1.3; a ratio <<1 over 90d means you're broadcasting more than you're receiving — investigate why."
    )
    out.append(
        "- **Stale-but-valuable** is the single most actionable number on this dashboard. "
        "Each one is a high-value contact going cold. The weekly job is to drive this number down — "
        "use `prepare-gmail-keepalive`, then approve drafts."
    )
    out.append(
        "- **Approval rate** measures whether the agent's drafts match your judgment. "
        "If it drifts below 50%, the keyword/value heuristics need tuning (or your goals need refreshing)."
    )
    out.append("")
    return "\n".join(out)
