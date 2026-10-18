"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL, checkHealth, type HealthStatus } from "./client";

const POLL_MS = 60_000;

/** Polls the backend `/health` endpoint. Falls back to "unconfigured" without an API URL. */
export function useBackendHealth(): HealthStatus {
  const [status, setStatus] = useState<HealthStatus>(API_BASE_URL ? "checking" : "unconfigured");

  useEffect(() => {
    if (!API_BASE_URL) return;
    let cancelled = false;
    const run = () => {
      checkHealth().then((next) => {
        if (!cancelled) setStatus(next);
      });
    };
    run();
    const timer = setInterval(run, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return status;
}
