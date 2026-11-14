from fastapi import APIRouter, HTTPException, Request, status

from app.api.schemas.simulation import SimulationStatus, StartSimulationRequest
from app.services.simulation_controller import (
    SimulationBusyError,
    SimulationConfigurationError,
    SimulationController,
    SimulationStateError,
)


router = APIRouter(prefix="/api/v1/simulation", tags=["simulation"])


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
