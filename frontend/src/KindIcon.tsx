import type { SourceKind } from "./types";
import { KIND_LABEL } from "./format";

// Simple line glyphs, one per kind of source, drawn on a 16-unit grid.
const PATHS: Record<SourceKind, string> = {
  document: "M4 1.5h5.5L12.5 4.5V14.5H4Z M9.5 1.5V4.5H12.5 M6 8h4.5 M6 10.5h4.5",
  text: "M3 3.5h10 M3 6.5h10 M3 9.5h10 M3 12.5h6",
  web: "M8 1.5a6.5 6.5 0 1 0 0 13a6.5 6.5 0 1 0 0-13 M1.5 8h13 M8 1.5c-2 2-2 11 0 13 M8 1.5c2 2 2 11 0 13",
  youtube: "M1.5 4.5h13v7h-13Z M6.5 6v4l3.5-2Z",
  video: "M1.5 4h9v8h-9Z M10.5 6.5l4-2v7l-4-2",
  audio: "M3 6v4 M5.5 4v8 M8 2v12 M10.5 4v8 M13 6v4",
  image: "M1.5 2.5h13v11h-13Z M1.5 11l4-4 3 3 2-2 4 4 M10.5 5.5a1 1 0 1 0 0.01 0",
};

export function KindIcon({ kind }: { kind: SourceKind }) {
  return (
    <svg className="kind-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.2"
      strokeLinecap="round" strokeLinejoin="round" role="img" aria-label={KIND_LABEL[kind]}>
      <path d={PATHS[kind]} />
    </svg>
  );
}
