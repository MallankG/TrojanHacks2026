/**
 * Thin, typed client for the TraceLayer FastAPI backend.
 * The UI runs on seeded synthetic data; this client is used for health checks today
 * and is the single place to wire real endpoints (alerts, cases, lineage, copilot).
 */

export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  timeoutMs?: number;
}

export async function apiRequest<T>(path: string, { body, timeoutMs = 8000, headers, ...init }: RequestOptions = {}): Promise<T> {
  if (!API_BASE_URL) throw new ApiError("API base URL is not configured", 0);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const isForm = body instanceof FormData;
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      // Multipart uploads let the browser set the boundary header itself.
      headers: { ...(isForm ? {} : { "Content-Type": "application/json" }), "X-Request-Id": crypto.randomUUID(), ...headers },
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
      signal: controller.signal,
    });
    const requestId = response.headers.get("x-request-id") ?? undefined;
    if (!response.ok) {
      // The backend returns safe error codes and a short `detail`, never stack traces.
      throw new ApiError(await errorDetail(response), response.status, requestId);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === "string") return data.detail;
    // FastAPI validation errors: [{loc, msg}, ...]
    if (Array.isArray(data.detail)) return data.detail.map((d: { msg?: string }) => d.msg ?? "Invalid input").join("; ");
  } catch {
    // Non-JSON body; fall through to the status code.
  }
  return `Request failed with status ${response.status}`;
}

export type HealthStatus = "unconfigured" | "checking" | "online" | "offline";

export async function checkHealth(): Promise<HealthStatus> {
  if (!API_BASE_URL) return "unconfigured";
  try {
    const result = await apiRequest<{ status: string }>("/health", { timeoutMs: 4000, cache: "no-store" });
    return result.status === "ok" ? "online" : "offline";
  } catch {
    return "offline";
  }
}
