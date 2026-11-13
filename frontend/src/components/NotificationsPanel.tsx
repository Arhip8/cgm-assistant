import { useState } from "react";

import type { InformationNotification } from "../types/api";
import { formatDateTime } from "./GlucoseCard";

interface NotificationsPanelProps {
  notifications: InformationNotification[];
  onAcknowledge: (notificationId: string) => Promise<void>;
}

export function NotificationsPanel({
  notifications,
  onAcknowledge,
}: NotificationsPanelProps) {
  const [pendingIds, setPendingIds] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const unreadCount = notifications.filter(
    (notification) => notification.status === "unread",
  ).length;

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
    <section
      className="panel notifications-panel"
      aria-labelledby="notifications-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">ИНФОРМАЦИЯ ДЛЯ СЕАНСА</p>
          <h2 id="notifications-title">Уведомления</h2>
        </div>
        <span
          className={`count-badge ${unreadCount > 0 ? "count-badge-unread" : ""}`}
          aria-label={`Непрочитанных: ${unreadCount}`}
        >
          {unreadCount}
        </span>
      </div>

      {error && (
        <p className="notification-error" role="alert">
          {error}
        </p>
      )}

      {notifications.length === 0 ? (
        <div className="notifications-empty">
          <span className="notification-empty-mark" aria-hidden="true">i</span>
          <strong>Уведомлений пока нет</strong>
          <span>Значимые события появятся здесь согласно правилам прототипа</span>
        </div>
      ) : (
        <ul className="notification-list">
          {notifications.map((notification) => {
            const isUnread = notification.status === "unread";
            const isPending = pendingIds.has(notification.id);

            return (
              <li
                className={`notification-item ${isUnread ? "notification-unread" : ""}`}
                key={notification.id}
              >
                <div className="notification-copy">
                  <div className="notification-title-row">
                    <strong>{notification.title}</strong>
                    <span
                      className={`notification-status ${isUnread ? "status-unread" : ""}`}
                    >
                      {isUnread ? "Новое" : "Ознакомлен"}
                    </span>
                  </div>
                  <p>{notification.text}</p>
                  <time dateTime={notification.created_at}>
                    {formatDateTime(notification.created_at)}
                  </time>
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
              </li>
            );
          })}
        </ul>
      )}
      <p className="notifications-note">
        Информационные уведомления прототипа; это не медицинские рекомендации.
      </p>
    </section>
  );
}
