export interface Reading {
  run_id: string;
  source_index: number;
  measured_at: string;
  glucose_mmol_l: number;
}

export type EventPhase = "started" | "ended" | "interrupted" | "observed";

export interface AnalysisEvent {
  event_id: string;
  run_id: string;
  event_type: string;
  phase: EventPhase;
  origin: string;
  priority_hint: string;
  started_at: string;
  ended_at: string | null;
  measured_at: string;
  glucose_mmol_l: number;
  details: Record<string, unknown>;
}

export type NotificationStatus = "unread" | "read";

export interface InformationNotification {
  id: string;
  run_id: string;
  event_id: string;
  title: string;
  text: string;
  created_at: string;
  status: NotificationStatus;
  acknowledged_at: string | null;
}

export type SimulationState =
  | "idle"
  | "running"
  | "paused"
  | "stopped"
  | "completed"
  | "error";

export interface SimulationStatus {
  run_id: string | null;
  status: SimulationState;
  total_readings: number;
  processed_readings: number;
  current_source_index: number;
  speed_multiplier: number;
  error: string | null;
}

export interface SimulationRunSummary {
  run_id: string;
  status: string;
  started_at: string;
  processed_readings: number;
}

export type SimulationAction = "start" | "pause" | "resume" | "stop";
