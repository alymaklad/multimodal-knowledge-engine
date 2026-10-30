// Shapes returned by the API (see app/schemas). Only the fields the interface reads are listed.

export type SourceKind = "document" | "web" | "youtube" | "image" | "audio" | "video" | "text";
export type SourceStatus = "pending" | "downloading" | "processing" | "completed" | "failed" | "needs_review";

export interface Source {
  id: number;
  kind: SourceKind;
  title: string;
  origin_url: string | null;
  original_filename: string | null;
  mime_type: string | null;
  author: string | null;
  description: string | null;
  summary: string | null;
  details: Record<string, unknown>;
  duration: number | null;
  status: SourceStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface Collection {
  id: number;
  name: string;
  description: string | null;
  source_count: number;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: number;
  source_id: number;
  status: "queued" | "running" | "completed" | "failed" | "needs_review";
  current_stage: string;
  error_message: string | null;
}

export interface Locator {
  page?: number;
  page_end?: number;
  section?: string;
  paragraph?: number;
  start?: number;
  end?: number;
  region?: string;
}

export interface Citation {
  marker: number;
  source_id: number;
  title: string;
  origin_url: string | null;
  chunk_key: string;
  content_type: string | null;
  locator: Locator;
  citation: string | null;
  snippet: string;
}

export interface ChatMessage {
  id: number;
  conversation_id: number;
  role: "user" | "assistant";
  content: string;
  status: "complete" | "streaming" | "failed" | "refused" | "interrupted";
  grounded: boolean;
  citations: Citation[];
  retrieval_query: string | null;
  model: string | null;
  error_message: string | null;
  created_at: string;
}

export interface Conversation {
  id: number;
  collection_id: number;
  title: string;
  source_ids: number[] | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

export interface StoredObject {
  id: number;
  kind: string;
  content_type: string;
  size_bytes: number;
  url: string;
}
