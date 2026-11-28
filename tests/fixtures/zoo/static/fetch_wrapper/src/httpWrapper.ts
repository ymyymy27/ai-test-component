export async function requestApi(path: string, method = "GET", payload?: Record<string, unknown>) {
  return fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : undefined,
  });
}

export function loadProfile() {
  return fetch("/api/profile", { method: "GET" });
}
