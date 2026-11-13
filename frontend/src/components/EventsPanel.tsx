import type { AnalysisEvent, EventPhase } from "../types/api";
import { formatDateTime } from "./GlucoseCard";

interface EventsPanelProps {
  events: AnalysisEvent[];
}

const phaseLabels: Record<EventPhase, string> = {
  started: "Начало",
  ended: "Завершено",
  interrupted: "Прервано",
  observed: "Зафиксировано",
};

const phaseClasses: Record<EventPhase, string> = {
  started: "phase-started",
  ended: "phase-ended",
  interrupted: "phase-interrupted",
  observed: "phase-observed",
};

const eventLabels: Record<string, string> = {
  demo_above_range: "Выше демонстрационной границы",
  demo_below_range: "Ниже демонстрационной границы",
  demo_rapid_rise: "Быстрый рост · демо",
  demo_rapid_fall: "Быстрое снижение · демо",
  data_gap: "Пауза в данных",
};

function eventLabel(eventType: string): string {
  return eventLabels[eventType] ?? eventType;
}

export function EventsPanel({ events }: EventsPanelProps) {
  const sortedEvents = [...events].sort(
    (left, right) =>
      new Date(right.measured_at).getTime() -
      new Date(left.measured_at).getTime(),
  );

  return (
    <section className="panel events-panel" aria-labelledby="events-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">НАБЛЮДЕНИЯ АЛГОРИТМА</p>
          <h2 id="events-title">События</h2>
        </div>
        <span className="count-badge">{events.length}</span>
      </div>

      {sortedEvents.length === 0 ? (
        <div className="events-empty">
          <span className="events-empty-mark" aria-hidden="true">✓</span>
          <strong>Событий пока нет</strong>
          <span>Новые наблюдения появятся здесь</span>
        </div>
      ) : (
        <ul className="event-list">
          {sortedEvents.map((event, index) => (
            <li
              className="event-item"
              key={`${event.event_id}-${event.phase}-${event.measured_at}-${index}`}
            >
              <span
                className={`event-marker ${phaseClasses[event.phase]}`}
                aria-hidden="true"
              />
              <div className="event-content">
                <div className="event-title-row">
                  <strong className="event-title">{eventLabel(event.event_type)}</strong>
                  <span className={`phase-badge ${phaseClasses[event.phase]}`}>
                    {phaseLabels[event.phase]}
                  </span>
                </div>
                <time className="event-time" dateTime={event.measured_at}>
                  {formatDateTime(event.measured_at)}
                </time>
                {event.ended_at && event.ended_at !== event.measured_at && (
                  <span className="event-detail">
                    Начало: {formatDateTime(event.started_at)}
                  </span>
                )}
                {event.priority_hint && (
                  <span className="event-hint">
                    Подсказка алгоритма: {event.priority_hint}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="events-note">
        Это события алгоритма, а не отправленные пользователю уведомления.
      </p>
    </section>
  );
}
