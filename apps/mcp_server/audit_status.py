from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from packages.database.models import AgentActionLog
from packages.database.session import SessionLocal


def main() -> None:
    since = datetime.now(UTC) - timedelta(days=7)
    with SessionLocal() as session:
        total = int(
            session.scalar(
                select(func.count()).select_from(AgentActionLog)
            )
            or 0
        )
        recent = int(
            session.scalar(
                select(func.count())
                .select_from(AgentActionLog)
                .where(AgentActionLog.created_at >= since)
            )
            or 0
        )
        rows = session.execute(
            select(
                AgentActionLog.tool_name,
                AgentActionLog.status,
                func.count(),
                func.avg(AgentActionLog.duration_ms),
            )
            .where(AgentActionLog.created_at >= since)
            .group_by(
                AgentActionLog.tool_name,
                AgentActionLog.status,
            )
            .order_by(func.count().desc())
        ).all()
    print(
        json.dumps(
            {
                "total": total,
                "last_7_days": recent,
                "by_tool": [
                    {
                        "tool_name": tool_name,
                        "status": status,
                        "count": int(count),
                        "average_duration_ms": round(float(avg or 0), 2),
                    }
                    for tool_name, status, count, avg in rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
