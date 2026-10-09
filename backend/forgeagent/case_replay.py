"""Read-only, paginated case presentation without event transitions or workspace bodies."""

from sqlalchemy import select

from . import db, service
from .domain import Fault, digest


def page(s, run, after=0, through_seq=None, limit=100):
    through_seq = run.seq if through_seq is None else through_seq
    if not 0 <= after <= through_seq <= run.seq:
        raise Fault("INVALID_CURSOR", "Replay cursor is outside committed history", 422)
    state = run.state
    if state.get("knowledge_erased"):
        return {"run_id": run.id, "through_seq": through_seq, "current_seq": run.seq,
                "status": "erased", "task": {"goal": "[erased]", "allowed_paths": [], "deliverables": []},
                "commit": None, "bindings": {"model": "", "harness": {}, "implementation_digest": "erased"},
                "verification": None, "events": [], "next_after": None, "read_only": True}
    events = s.execute(select(db.Event.seq, db.Event.type, db.Event.created_at,
        db.Event.payload["message"].as_string().label("message")).where(db.Event.tenant_id == run.tenant_id,
            db.Event.run_id == run.id, db.Event.seq > after, db.Event.seq <= through_seq)
        .order_by(db.Event.seq).limit(limit + 1)).all()
    return {"run_id": run.id, "through_seq": through_seq, "current_seq": run.seq,
        "status": run.status, "task": state["task"], "commit": (state.get("repository") or {}).get("commit"),
        "bindings": {"model": state["semantic"]["model_id"], "harness": state["semantic"].get("harness", {}),
                     "implementation_digest": digest(state["semantic"]["implementation"])},
        "verification": service.public_report(state["acceptance"], state["verification"]) if state.get("verification") else None,
        "events": [{"seq": e.seq, "type": e.type, "time": e.created_at.isoformat(), "message": e.message} for e in events[:limit]],
        "next_after": events[limit - 1].seq if len(events) > limit else None,
        "read_only": True}
