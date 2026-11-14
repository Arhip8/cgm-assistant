import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { initializeSession } from "./api/client";
import "./styles.css";

const root = ReactDOM.createRoot(document.getElementById("root")!);

async function start() {
  try {
    await initializeSession();
  } catch (error) {
    root.render(<main><p role="alert">{error instanceof Error ? error.message : "Сервер недоступен"}</p><button onClick={() => void start()}>Повторить подключение</button></main>);
    return;
  }
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}
void start();
