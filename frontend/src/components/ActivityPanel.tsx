import { useMemo, useState } from "react";

import type {
  AnalysisEvent,
  EventPhase,
  InformationNotification,
} from "../types/api";
import { formatGlucose } from "../constants";
import { formatDateTime } from "./GlucoseCard";

interface ActivityPanelProps {
  notifications: InformationNotification[];
  events: AnalysisEvent[];
  onAcknowledge: (notificationId: string) => Promise<void>;
}

type ActivityTab = "notifications" | "events";
type NotificationFilter = "all" | "unread";

const phaseLabels: Record<EventPhase, string> = {
  started: "Начало",
  ended: "Завершение",
  interrupted: "Прервано",
  observed: "Зафиксировано",
};

const eventLabels: Record<string, string> = {
  demo_above_range: "Глюкоза выше диапазона",
  demo_below_range: "Глюкоза ниже диапазона",
  demo_rapid_rise: "Быстрое повышение глюкозы",
  demo_rapid_fall: "Быстрое снижение глюкозы",
  data_gap: "Пауза в данных",
};

export function ActivityPanel({
  notifications,
  events,
  onAcknowledge,
}: ActivityPanelProps) {
  const [activeTab, setActiveTab] = useState<ActivityTab>("notifications");
  const [filter, setFilter] = useState<NotificationFilter>("all");
  const [expanded, setExpanded] = useState(false);
  const [pendingIds, setPendingIds] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);

  const unreadCount = notifications.filter(
    (notification) => notification.status === "unread",
  ).length;
  const sortedNotifications = useMemo(
    () =>
      [...notifications].sort((left, right) => {
        const statusOrder =
          Number(right.status === "unread") - Number(left.status === "unread");
        return (
          statusOrder ||
          new Date(right.created_at).getTime() -
            new Date(left.created_at).getTime()
        );
      }),
    [notifications],
  );
  const visibleNotifications = sortedNotifications.filter(
    (notification) => filter === "all" || notification.status === "unread",
  );
  const displayedNotifications = expanded
    ? visibleNotifications
    : visibleNotifications.slice(0, 3);
  const sortedEvents = useMemo(
    () =>
      [...events].sort(
        (left, right) =>
          new Date(right.measured_at).getTime() -
          new Date(left.measured_at).getTime(),
      ),
    [events],
  );
  const displayedEvents = expanded ? sortedEvents : sortedEvents.slice(0, 3);
  const eventsById = useMemo(
    () => {
      const byId = new Map<string, AnalysisEvent>();
      for (const event of events) {
        const existing = byId.get(event.event_id);
        if (!existing || event.phase === "started") {
          byId.set(event.event_id, event);
        }
      }
      return byId;
    },
    [events],
  );

  const handleAcknowledge = async (notificationId: string) => {
    setPendingIds((current) => new Set(current).add(notificationId));
    setError(null);
    try {
      await onAcknowledge(notificationId);
    } catch (acknowledgementError) {
      setError(
        acknowledgementError instanceof Error
          ? acknowledgementError.message
          : "Не удалось подтвердить уведомление.",
      );
    } finally {
      setPendingIds((current) => {
        const next = new Set(current);
        next.delete(notificationId);
        return next;
      });
    }
  };

  return (
    <section className="panel activity-panel" aria-label="Уведомления и события">
      <div className="activity-tabs" role="tablist" aria-label="Журнал">
        <button
          id="activity-tab-notifications"
          className="activity-tab"
          type="button"
          role="tab"
          aria-selected={activeTab === "notifications"}
          aria-controls="activity-panel"
          onClick={() => {
            setActiveTab("notifications");
            setExpanded(false);
          }}
        >
          Уведомления <span>{notifications.length}</span>
        </button>
        <button
          id="activity-tab-events"
          className="activity-tab"
          type="button"
          role="tab"
          aria-selected={activeTab === "events"}
          aria-controls="activity-panel"
          onClick={() => {
            setActiveTab("events");
            setExpanded(false);
          }}
        >
          События <span>{events.length}</span>
        </button>
      </div>

      {activeTab === "notifications" ? (
        <div
          id="activity-panel"
          className="activity-content"
          role="tabpanel"
          aria-labelledby="activity-tab-notifications"
        >
          <div className="activity-toolbar">
            <span className="activity-unread-count">
              Новых: <strong>{unreadCount}</strong>
            </span>
            <div className="activity-filter" role="group" aria-label="Фильтр уведомлений">
              <button
                type="button"
                aria-pressed={filter === "all"}
                onClick={() => {
                  setFilter("all");
                  setExpanded(false);
                }}
              >
                Все
              </button>
              <button
                type="button"
                aria-pressed={filter === "unread"}
                onClick={() => {
                  setFilter("unread");
                  setExpanded(false);
                }}
              >
                Новые
              </button>
            </div>
          </div>

          {error && (
            <p className="activity-error" role="alert">
              {error}
            </p>
          )}

          {visibleNotifications.length === 0 ? (
            <p className="activity-empty">
              {filter === "unread"
                ? "Новых уведомлений нет"
                : "Уведомлений пока нет"}
            </p>
          ) : (
            <ul id="activity-notification-list" className="activity-list">
              {displayedNotifications.map((notification) => {
                const isUnread = notification.status === "unread";
                const isPending = pendingIds.has(notification.id);
                const event = eventsById.get(notification.event_id);
                const relatedEventTypes = event?.details["related_event_types"];
                const isCombinedLowFall =
                  Array.isArray(relatedEventTypes) &&
                  relatedEventTypes.includes("demo_rapid_fall");

                return (
                  <li
                    className={`activity-item ${isUnread ? "activity-item-new" : ""}`}
                    key={notification.id}
                  >
                    <div className="activity-item-heading">
                        <strong>
                          {isCombinedLowFall
                            ? notification.title
                            : event
                              ? eventLabels[event.event_type] ?? event.event_type
                              : notification.title}
                        </strong>
                        {isUnread && <span className="new-indicator">Новое</span>}
                      </div>
                      <p>
                        {isCombinedLowFall
                          ? notification.text
                          : event
                            ? `${formatGlucose(event.glucose_mmol_l)} ммоль/л`
                            : notification.text}
                      </p>
                      <div className="activity-item-footer">
                        <div className="notification-times">
                          {event && (
                            <span>
                              Измерение{" "}
                              <time dateTime={event.measured_at}>
                                {formatDateTime(event.measured_at)}
                              </time>
                            </span>
                          )}
                          <span>
                            Создано{" "}
                            <time dateTime={notification.created_at}>
                              {formatDateTime(notification.created_at)}
                            </time>
                          </span>
                        </div>
                        {isUnread && (
                        <button
                          className="acknowledge-button"
                          type="button"
                          disabled={isPending}
                          onClick={() => void handleAcknowledge(notification.id)}
                        >
                          {isPending ? "Сохраняем…" : "Я увидел"}
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {visibleNotifications.length > 3 && (
            <button
              className="activity-expand"
              type="button"
              aria-expanded={expanded}
              aria-controls="activity-notification-list"
              onClick={() => setExpanded((current) => !current)}
            >
              {expanded
                ? "Свернуть"
                : `Показать все (${visibleNotifications.length})`}
            </button>
          )}
        </div>
      ) : (
        <div
          id="activity-panel"
          className="activity-content"
          role="tabpanel"
          aria-labelledby="activity-tab-events"
        >
          {sortedEvents.length === 0 ? (
            <p className="activity-empty">Событий пока нет</p>
          ) : (
            <ul id="activity-event-list" className="activity-list">
              {displayedEvents.map((event, index) => (
                <li
                  className="activity-item event-record"
                  key={`${event.event_id}-${event.phase}-${event.measured_at}-${index}`}
                >
                  <div className="activity-item-heading">
                    <strong>{eventLabels[event.event_type] ?? event.event_type}</strong>
                    <span className={`phase-label phase-${event.phase}`}>
                      {phaseLabels[event.phase]}
                    </span>
                  </div>
                  <p>{formatGlucose(event.glucose_mmol_l)} ммоль/л</p>
                  <time dateTime={event.measured_at}>
                    {formatDateTime(event.measured_at)}
                  </time>
                </li>
              ))}
            </ul>
          )}
          {sortedEvents.length > 3 && (
            <button
              className="activity-expand"
              type="button"
              aria-expanded={expanded}
              aria-controls="activity-event-list"
              onClick={() => setExpanded((current) => !current)}
            >
              {expanded ? "Свернуть" : `Показать все (${sortedEvents.length})`}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
