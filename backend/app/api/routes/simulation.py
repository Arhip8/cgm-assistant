from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.schemas.simulation import (
    SimulationRunSummary,
    SimulationStatus,
    StartSimulationRequest,
)
from app.services.simulation_controller import (
    SimulationBusyError,
    SimulationConfigurationError,
    SimulationController,
    SimulationStateError,
)


router = APIRouter(prefix="/api/v1/simulation", tags=["simulation"])


@router.get("/runs", response_model=list[SimulationRunSummary])
def list_simulation_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[SimulationRunSummary]:
    processor = request.app.state.reading_processor
    return [
        SimulationRunSummary.model_validate(item)
        for item in processor.list_server_simulations(limit=limit)
    ]


@router.post("/start", response_model=SimulationStatus)
def start_simulation(
    payload: StartSimulationRequest,
    request: Request,
) -> SimulationStatus:
    controller: SimulationController = request.app.state.simulation_controller
    try:
        return SimulationStatus.model_validate(
            controller.start(payload.speed_multiplier)
        )
    except SimulationBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SimulationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/pause", response_model=SimulationStatus)
def pause_simulation(request: Request) -> SimulationStatus:
    controller: SimulationController = request.app.state.simulation_controller
    try:
        return SimulationStatus.model_validate(controller.pause())
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post("/resume", response_model=SimulationStatus)
def resume_simulation(request: Request) -> SimulationStatus:
    controller: SimulationController = request.app.state.simulation_controller
    try:
        return SimulationStatus.model_validate(controller.resume())
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except SimulationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/stop", response_model=SimulationStatus)
def stop_simulation(request: Request) -> SimulationStatus:
    controller: SimulationController = request.app.state.simulation_controller
    try:
        return SimulationStatus.model_validate(controller.stop())
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("/status", response_model=SimulationStatus)
def get_simulation_status(request: Request) -> SimulationStatus:
    controller: SimulationController = request.app.state.simulation_controller
    return SimulationStatus.model_validate(controller.status())
