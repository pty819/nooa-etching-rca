export async function api(path: string, method = "GET", data?: unknown) {
  const response = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-Research-Client": "1" },
    body: data ? JSON.stringify(data) : undefined,
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail || "请求失败");
  }
  return response.json();
}
export function asset(session: string, path: string) {
  return (
    "/api/sessions/" +
    encodeURIComponent(session) +
    "/artifacts/" +
    path.split("/").map(encodeURIComponent).join("/")
  );
}
