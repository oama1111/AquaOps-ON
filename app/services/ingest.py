"""Ingest service (M1): parse a laboratory CSV and upsert it idempotently.

The idempotency rule is the point of this module. A laboratory export that is
re-sent — because the e-mail bounced, because the operator clicked twice, because
the same file arrives by two routes — must not duplicate or overwrite anything.
The unique key is (sampling point, measured time); a row that already exists is
counted and skipped, and the receipt says so.

Nothing is written until the whole file has been classified, so the stored
receipt and the stored readings can never disagree with each other.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChlorineReading, LabResultUpload, SamplingPoint
from app.schemas.registry import RowError
from modules.m1_ingest import LabParseResult, parse_lab_csv


def known_point_ids(session: Session, system_id: str) -> set[str]:
    rows = session.execute(
        select(SamplingPoint.id).where(SamplingPoint.system_id == system_id)
    ).scalars()
    return set(rows)


def ingest_lab_csv(
    session: Session,
    system_id: str,
    filename: str,
    content: bytes,
) -> tuple[LabResultUpload, LabParseResult, int]:
    """Parse, validate, and upsert one laboratory file.

    Returns the provenance row, the parse result, and the number of readings
    that were genuinely new. Replaying the identical file yields
    ``rows_accepted == 0`` and every row recognised as a duplicate.
    """
    digest = hashlib.sha256(content).hexdigest()
    with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    try:
        parsed = parse_lab_csv(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)

    existing = {
        (point_id, measured_at)
        for point_id, measured_at in session.execute(
            select(ChlorineReading.sampling_point_id, ChlorineReading.measured_at)
        ).all()
    }
    registered = known_point_ids(session, system_id)

    pending: list[ChlorineReading] = []
    duplicates = 0
    upload_id = str(uuid.uuid4())

    for reading in parsed.readings:
        if reading.sampling_point_id not in registered:
            parsed.reject(
                reading,
                RowError(
                    row_number=0,
                    field="sampling_point_id",
                    message=f"point {reading.sampling_point_id} is not registered",
                ),
            )
            continue
        key = (reading.sampling_point_id, reading.measured_at)
        if key in existing:
            duplicates += 1
            continue  # already ingested: an idempotent replay, not an error
        existing.add(key)
        pending.append(
            ChlorineReading(
                sampling_point_id=reading.sampling_point_id,
                measured_at=reading.measured_at,
                free_chlorine_mg_l=reading.free_chlorine_mg_l,
                source=reading.source.value,
                upload_id=upload_id,
            )
        )

    upload = LabResultUpload(
        upload_id=upload_id,
        system_id=system_id,
        filename=filename,
        sha256=digest,
        rows_total=parsed.rows_total,
        rows_accepted=len(pending),
        rows_rejected=parsed.rows_rejected,
        row_errors=json.dumps([error.model_dump() for error in parsed.errors]),
    )
    session.add(upload)
    session.add_all(pending)
    session.commit()
    return upload, parsed, len(pending)


def ingest_history(session: Session, system_id: str) -> list[LabResultUpload]:
    rows = session.execute(
        select(LabResultUpload)
        .where(LabResultUpload.system_id == system_id)
        .order_by(LabResultUpload.received_at.desc())
    ).scalars()
    return list(rows)
