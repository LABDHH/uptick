import type { Progress, Results } from "../types";

/**
 * POST /api/search returns Server-Sent Events, not JSON, because a cold query
 * takes roughly 50 to 60 seconds. EventSource cannot issue a POST, so the
 * stream is parsed manually from the fetch body.
 */
export async function runSearch(
  brief: string,
  sessionId: string,
  onProgress: (p: Progress) => void,
  signal?: AbortSignal,
): Promise<Results> {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brief, session_id: sessionId }),
    signal,
  });

  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : msg;
    } catch { /* body was not JSON */ }
    throw new Error(msg);
  }
  if (!res.body) throw new Error("No response stream.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: Results | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;

      let payload: unknown;
      try { payload = JSON.parse(dataLines.join("\n")); } catch { continue; }

      if (event === "progress") onProgress(payload as Progress);
      else if (event === "result") result = payload as Results;
      else if (event === "error") {
        throw new Error((payload as { message?: string })?.message ?? "Search failed.");
      }
    }
  }

  if (!result) throw new Error("The search ended without returning results.");
  return result;
}

export async function loadDemo(): Promise<Results> {
  const res = await fetch("/api/demo");
  if (!res.ok) throw new Error("Could not load the example.");
  return res.json();
}
