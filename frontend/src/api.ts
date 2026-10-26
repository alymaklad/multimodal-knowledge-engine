import type { ChatMessage, Collection, Conversation, ConversationDetail, Job, Source, StoredObject } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(message);
  }

  /** The id of the source a 409 says already exists, when there is one. */
  get existingSourceId(): number | null {
    const detail = this.detail as { source_id?: number } | null;
    return this.status === 409 && detail && typeof detail.source_id === "number" ? detail.source_id : null;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError("The server could not be reached. Check that the API is running.", 0, null);
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(describe(body, response.status), response.status, body?.detail ?? null);
  return body as T;
}

function describe(body: { detail?: unknown } | null, status: number): string {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: string }).message);
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return `The request failed (${status}).`;
}

const json = (value: unknown) => JSON.stringify(value);

export const api = {
  collections: () => request<Collection[]>("/api/collections"),
  createCollection: (name: string) => request<Collection>("/api/collections", { method: "POST", body: json({ name }) }),
  renameCollection: (id: number, name: string, description: string | null) =>
    request<Collection>(`/api/collections/${id}`, { method: "PATCH", body: json({ name, description }) }),
  deleteCollection: (id: number) => request<void>(`/api/collections/${id}`, { method: "DELETE" }),
  addToCollection: (id: number, sourceIds: number[]) =>
    request<{ added: number[] }>(`/api/collections/${id}/sources`, { method: "POST", body: json({ source_ids: sourceIds }) }),
  removeFromCollection: (id: number, sourceId: number) =>
    request<void>(`/api/collections/${id}/sources/${sourceId}`, { method: "DELETE" }),

  sources: (collectionId?: number) =>
    request<Source[]>(`/api/sources?limit=100${collectionId ? `&collection_id=${collectionId}` : ""}`),
  deleteSource: (id: number) => request<void>(`/api/sources/${id}`, { method: "DELETE" }),
  sourceMedia: (id: number) => request<StoredObject[]>(`/api/sources/${id}/media`),
  addUrl: (url: string, collectionId?: number) =>
    request<{ source_id: number }>("/api/sources/url", { method: "POST", body: json({ url, collection_id: collectionId }) }),
  addText: (title: string, text: string, collectionId?: number) =>
    request<{ source_id: number }>("/api/sources/text", {
      method: "POST",
      body: json({ title, text, collection_id: collectionId }),
    }),
  upload: (file: File, collectionId?: number) => {
    const form = new FormData();
    form.append("media", file);
    if (collectionId) form.append("collection_id", String(collectionId));
    return request<{ source_id: number }>("/api/sources/upload", { method: "POST", body: form });
  },

  jobForSource: (sourceId: number) => request<Job>(`/api/jobs/source/${sourceId}`),
  dispatch: (jobId: number) => request<{ task_id: string }>(`/api/jobs/${jobId}/dispatch`, { method: "POST" }),
  retry: (jobId: number) => request<{ task_id: string }>(`/api/jobs/${jobId}/retry`, { method: "POST" }),

  conversations: (collectionId: number) => request<Conversation[]>(`/api/collections/${collectionId}/conversations`),
  createConversation: (collectionId: number, sourceIds: number[] | null) =>
    request<Conversation>(`/api/collections/${collectionId}/conversations`, {
      method: "POST",
      body: json({ source_ids: sourceIds }),
    }),
  conversation: (id: number) => request<ConversationDetail>(`/api/conversations/${id}`),
  setConversationScope: (id: number, sourceIds: number[] | null) =>
    request<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: json({ source_ids: sourceIds }) }),
  deleteConversation: (id: number) => request<void>(`/api/conversations/${id}`, { method: "DELETE" }),
};

/** Queue a new source for processing. Processing starts in the worker, not in the request. */
export async function startProcessing(sourceId: number): Promise<void> {
  const job = await api.jobForSource(sourceId);
  await api.dispatch(job.id);
}

export type StreamEvent =
  | { type: "user_message"; data: ChatMessage }
  | { type: "retrieval"; data: { query: string; passages: number } }
  | { type: "delta"; data: { text: string } }
  | { type: "done"; data: ChatMessage }
  | { type: "error"; data: { status: string; message: string; message_id?: number } };

/** Ask a question and receive the answer as Server-Sent Events, which EventSource cannot POST. */
export async function streamAnswer(
  conversationId: number,
  content: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: json({ content }),
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    throw new ApiError(describe(body, response.status), response.status, body?.detail ?? null);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const type = /^event: (.+)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (type && data) onEvent({ type, data: JSON.parse(data) } as StreamEvent);
      boundary = buffer.indexOf("\n\n");
    }
  }
}
