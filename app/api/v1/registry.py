"""Registry and ingest endpoints (M1), `docs/api-spec.md` §1."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import LabResultUpload, SamplingPoint, Vehicle, WaterSystem
from app.schemas.enums import IngestStatus, LocationType
from app.schemas.registry import (
    IngestReceipt,
    IngestStatusOut,
    RowError,
    SamplingPointOut,
    SystemOut,
)
from app.services.ingest import ingest_lab_csv

router = APIRouter()


@router.get("/systems/{system_id}", response_model=SystemOut)
def get_system(system_id: str, session: Session = Depends(get_session)) -> SystemOut:
    system = session.get(WaterSystem, system_id)
    if system is None:
        raise HTTPException(status_code=404, detail=f"unknown system: {system_id}")
    point_count = len(
        list(
            session.execute(
                select(SamplingPoint.id).where(SamplingPoint.system_id == system_id)
            ).scalars()
        )
    )
    vehicle_count = len(
        list(
            session.execute(select(Vehicle.id).where(Vehicle.system_id == system_id)).scalars()
        )
    )
    return SystemOut(
        id=system.id,
        name=system.name,
        population=system.population,
        network_inp=system.network_inp,
        point_count=point_count,
        vehicle_count=vehicle_count,
    )


@router.get("/systems/{system_id}/sampling-points", response_model=list[SamplingPointOut])
def list_sampling_points(
    system_id: str, session: Session = Depends(get_session)
) -> list[SamplingPointOut]:
    rows = session.execute(
        select(SamplingPoint)
        .where(SamplingPoint.system_id == system_id)
        .order_by(SamplingPoint.id)
    ).scalars()
    return [
        SamplingPointOut(
            id=row.id,
            system_id=row.system_id,
            node_id=row.node_id,
            x_m=row.x_m,
            y_m=row.y_m,
            elevation_m=row.elevation_m,
            location_type=LocationType(row.location_type),
            active=row.active,
        )
        for row in rows
    ]


@router.post(
    "/systems/{system_id}/ingest/lab-csv",
    response_model=IngestReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_lab_results(
    system_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> IngestReceipt:
    """Accept a laboratory CSV.

    Acceptance is asynchronous by contract, but the receipt already tells the
    operator how many rows were read, how many were refused, and — on a replay —
    how many were recognised as duplicates rather than re-inserted.
    """
    content = await file.read()
    upload, parsed, inserted = ingest_lab_csv(
        session, system_id, file.filename or "upload.csv", content
    )
    if parsed.rows_total == 0:
        raise HTTPException(status_code=400, detail="CSV contained no data rows")
    return IngestReceipt(
        upload_id=upload.upload_id,
        rows_total=parsed.rows_total,
        rows_accepted=inserted,
        rows_rejected=parsed.rows_rejected,
    )


@router.get("/ingest/{upload_id}", response_model=IngestStatusOut)
def ingest_status(upload_id: str, session: Session = Depends(get_session)) -> IngestStatusOut:
    """The stored receipt, including the per-row refusals recorded at ingest time."""
    upload = session.get(LabResultUpload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail=f"unknown upload: {upload_id}")

    if upload.rows_rejected == 0:
        state = "accepted"
    elif upload.rows_accepted == 0:
        state = "rejected"
    else:
        state = "partial"

    errors = [RowError(**item) for item in json.loads(upload.row_errors or "[]")]
    return IngestStatusOut(
        upload_id=upload.upload_id,
        status=IngestStatus(state),
        rows_accepted=upload.rows_accepted,
        rows_rejected=upload.rows_rejected,
        row_errors=errors,
    )
