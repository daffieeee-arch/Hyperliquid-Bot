/**
 * Fetch a cockpit API route with `cache: no-store` and surface its fail-closed
 * `{ error }` body as the thrown message when the status is not OK.
 */
export async function fetchJsonOrThrow(url: string, fallbackMessage: string): Promise<unknown> {
  const response = await fetch(url, { cache: "no-store" });
  const payload: unknown = await response.json();
  if (!response.ok) {
    const message =
      typeof payload === "object" &&
      payload !== null &&
      "error" in payload &&
      typeof payload.error === "string"
        ? payload.error
        : `${fallbackMessage} (${String(response.status)})`;
    throw new Error(message);
  }
  return payload;
}
