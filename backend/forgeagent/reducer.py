"""Pure, versioned event reduction and verified checkpoint-based reconstruction."""

from copy import deepcopy

from .domain import Fault, digest


class EventReducer:
    version = 2

    @staticmethod
    def transition(previous, current):
        old_state, new_state = previous.get("state", {}), current.get("state", {})
        return {
            "fields": {k: v for k, v in current.items() if k != "state" and (k not in previous or previous[k] != v)},
            "state_set": {k: v for k, v in new_state.items() if k not in old_state or old_state[k] != v},
            "state_remove": sorted(old_state.keys() - new_state.keys()),
        }

    @staticmethod
    def apply(previous, payload):
        version = payload.get("schema_version")
        if version == 1:
            if not isinstance(payload.get("projection"), dict):
                raise Fault("EVENT_CORRUPT", "Legacy event has no projection")
            return deepcopy(payload["projection"])
        if version != 2:
            raise Fault("EVENT_SCHEMA", "This runtime cannot reduce the event schema")
        if digest(previous) != payload.get("prior_digest"):
            raise Fault("EVENT_DIVERGENCE", "Event does not extend the preceding committed state")
        transition = payload["transition"]
        state = deepcopy(previous.get("state", {}))
        state.update(deepcopy(transition["state_set"]))
        for key in transition["state_remove"]:
            if key not in state:
                raise Fault("EVENT_CORRUPT", "Event removes a nonexistent state field")
            del state[key]
        result = {**deepcopy(previous), **deepcopy(transition["fields"]), "state": state}
        if digest(result) != payload.get("projection_digest"):
            raise Fault("EVENT_CORRUPT", "Reduced projection digest mismatch")
        return result


def rebuild(s, tenant, run_id, through_seq=None, checkpoint_id=None):
    from sqlalchemy import BigInteger, cast, select

    from . import db

    run = db.get(s, db.Run, tenant, run_id)
    through_seq = run.seq if through_seq is None else through_seq
    if not 0 <= through_seq <= run.seq:
        raise Fault("INVALID_CURSOR", "Reconstruction sequence is outside committed history", 422)
    cls = db.Checkpoint
    event_seq = cast(cls.data["event_seq"].as_string(), BigInteger)
    query = select(cls).where(cls.tenant_id == tenant, cls.run_id == run_id, cls.status == "READY",
                              cls.data["schema_version"].as_string() == "2", event_seq <= through_seq)
    if checkpoint_id:
        query = query.where(cls.id == checkpoint_id)
    checkpoint = s.scalar(query.order_by(event_seq.desc(), cls.id.desc()).limit(1))
    if checkpoint_id and not checkpoint:
        raise Fault("CHECKPOINT_SCOPE", "Checkpoint is incompatible or outside the requested event range", 422)
    projection, seq = {}, 0
    if checkpoint:
        from .service import checkpoint_manifest

        manifest = checkpoint_manifest(tenant, checkpoint)
        if digest(manifest["state"]) != manifest["state_digest"] or manifest["projection"]["state"] != manifest["state"]:
            raise Fault("CHECKPOINT_CORRUPT", "Checkpoint projection or state digest mismatch")
        if manifest["event_seq"] != checkpoint.data["event_seq"]:
            raise Fault("CHECKPOINT_CORRUPT", "Checkpoint cursor differs from its stored manifest")
        anchor = s.scalar(select(db.Event).where(db.Event.tenant_id == tenant, db.Event.run_id == run_id,
                                                db.Event.seq == manifest["event_seq"]))
        if not anchor or digest(manifest["projection"]) != anchor.payload.get("projection_digest"):
            raise Fault("CHECKPOINT_CORRUPT", "Checkpoint projection is not bound to its committed event")
        projection, seq = manifest["projection"], manifest["event_seq"]
    events = s.scalars(select(db.Event).where(db.Event.tenant_id == tenant, db.Event.run_id == run_id,
                                            db.Event.seq > seq, db.Event.seq <= through_seq).order_by(db.Event.seq))
    for event in events:
        if event.seq != seq + 1:
            raise Fault("EVENT_GAP", "Committed event history is not contiguous")
        projection = EventReducer.apply(projection, event.payload)
        seq = event.seq
    if seq != through_seq:
        raise Fault("EVENT_GAP", "Committed history does not reach the requested sequence")
    return projection
