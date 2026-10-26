import type { Source, SourceKind, SourceStatus } from "./types";

export const KIND_LABEL: Record<SourceKind, string> = {
  document: "Document",
  web: "Web page",
  youtube: "YouTube video",
  image: "Image",
  audio: "Audio",
  video: "Video",
  text: "Note",
};

const WORKING: SourceStatus[] = ["pending", "downloading", "processing"];

export function isWorking(source: Source): boolean {
  return WORKING.includes(source.status);
}

export function statusText(source: Source): string | null {
  switch (source.status) {
    case "pending":
      return "Waiting to be processed";
    case "downloading":
      return "Fetching";
    case "processing":
      return "Reading";
    case "failed":
      return source.error_message ? `Couldn't be read: ${source.error_message}` : "Couldn't be read";
    case "needs_review":
      return "Ready, flagged for review";
    default:
      return null;
  }
}

export function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function formatDuration(seconds: number | null): string | null {
  if (seconds == null) return null;
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${minutes}:${String(rest).padStart(2, "0")}`;
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}
