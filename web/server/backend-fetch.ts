/** Workerd supports manual/follow, not redirect:error. Never forward backend credentials. */
export async function backendFetch(url: URL, init: RequestInit): Promise<Response> {
  const response = await fetch(url, { ...init, redirect: "manual" });
  if (response.status >= 300 && response.status < 400) {
    throw new Error("Backend redirects are not allowed.");
  }
  return response;
}
