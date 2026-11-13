interface DashboardHeaderProps {
  runId: string;
  onRunIdChange: (runId: string) => void;
}

export function DashboardHeader({
  runId,
  onRunIdChange,
}: DashboardHeaderProps) {
  return (
    <header className="topbar">
      <a className="brand" href="/" aria-label="CGM Assistant — главная">
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32" fill="none">
            <path
              d="M4 17h6l3.2-8 5.2 15 3.1-7H28"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="brand-name">
          <strong>cgm</strong>
          <span>assistant</span>
        </span>
      </a>

      <div className="header-controls">
        <label className="run-picker">
          <span>Сеанс</span>
          <input
            aria-label="Идентификатор сеанса"
            value={runId}
            onChange={(event) => onRunIdChange(event.target.value)}
            placeholder="Введите run_id"
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <span className="prototype-chip">
          <span className="chip-dot" />
          Демоверсия
        </span>
      </div>
    </header>
  );
}
