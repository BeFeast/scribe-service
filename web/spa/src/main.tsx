import React from "react";
import { createRoot } from "react-dom/client";

function App() {
  return <h1>Scribe SPA</h1>;
}

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Missing #root element");
}

createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
