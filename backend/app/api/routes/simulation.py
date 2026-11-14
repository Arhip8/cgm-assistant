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


def _control(request: Request, action: str, speed: float | None = None):
    manager = request.app.state.demo_sessions
    if manager is not None:
        return manager.control(request.state.demo_session, action, speed)
    controller: SimulationController = request.state.simulation_controller
    if action == "start":
        return controller.start(speed)
    return getattr(controller, action)()


@router.get("/runs", response_model=list[SimulationRunSummary])
def list_simulation_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[SimulationRunSummary]:
    processor = request.state.reading_processor
    return [
        SimulationRunSummary.model_validate(item)
        for item in processor.list_server_simulations(limit=limit)
    ]


@router.post("/start", response_model=SimulationStatus)
def start_simulation(
    payload: StartSimulationRequest,
    request: Request,
) -> SimulationStatus:
    try:
        return SimulationStatus.model_validate(
            _control(request, "start", payload.speed_multiplier)
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
    try:
        return SimulationStatus.model_validate(_control(request, "pause"))
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post("/resume", response_model=SimulationStatus)
def resume_simulation(request: Request) -> SimulationStatus:
    try:
        return SimulationStatus.model_validate(_control(request, "resume"))
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
    try:
        return SimulationStatus.model_validate(_control(request, "stop"))
    except SimulationStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.get("/status", response_model=SimulationStatus)
def get_simulation_status(request: Request) -> SimulationStatus:
    controller: SimulationController = request.state.simulation_controller
    return SimulationStatus.model_validate(controller.status())
