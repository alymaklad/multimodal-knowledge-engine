import { Fragment, type ReactNode } from "react";
import type { Citation } from "../types";

interface Props {
  text: string;
  citations: Citation[];
  streaming?: boolean;
  activeMarker?: number | null;
  /** `claim` is the sentence the marker is attached to, so the passage can show what supports it. */
  onCite?: (citation: Citation, claim: string) => void;
}

const MARKER = /(\[\d+\])/;
const MARKERS = /\[\d+\]/g;

/** The sentence a marker belongs to: the one ending just before it, or the text after it. */
function claimFor(parts: string[], index: number): string {
  const before = parts.slice(0, index).join("").replace(MARKERS, "").trim();
  const sentences = before.split(/(?<=[.!?])\s+/).filter(Boolean);
  const last = sentences[sentences.length - 1];
  return last || parts.slice(index + 1).join("").replace(MARKERS, "").trim();
}
const BULLET = /^\s*[-*•]\s+/;

type Block = { kind: "p" | "ul"; lines: string[] };

/** Blocks break at blank lines; inside one, consecutive bullet lines become a list. */
function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const chunk of text.split(/\n{2,}/)) {
    let current: Block | null = null;
    for (const raw of chunk.split("\n")) {
      if (!raw.trim()) continue;
      const kind = BULLET.test(raw) ? "ul" : "p";
      const line = kind === "ul" ? raw.replace(BULLET, "") : raw;
      if (current && current.kind === kind) {
        current.lines.push(line);
      } else {
        current = { kind, lines: [line] };
        blocks.push(current);
      }
    }
  }
  return blocks;
}

/**
 * An answer as prose, with each [n] marker drawn as a highlighter swipe that opens its passage.
 * While the answer is still streaming, markers are shown but not yet linked: citations arrive with
 * the finished answer.
 */
export function Answer({ text, citations, streaming, activeMarker, onCite }: Props) {
  const byMarker = new Map(citations.map((citation) => [citation.marker, citation]));

  function inline(line: string): ReactNode[] {
    const parts = line.split(MARKER);
    return parts.map((part, index) => {
      const match = /^\[(\d+)\]$/.exec(part);
      if (!match) return <Fragment key={index}>{part}</Fragment>;
      const marker = Number(match[1]);
      const citation = byMarker.get(marker);
      return (
        <button key={index} type="button" className="cite" disabled={!citation} aria-pressed={activeMarker === marker}
          aria-label={citation ? `Source ${marker}: ${citation.title}${citation.citation ? `, ${citation.citation}` : ""}` : `Source ${marker}`}
          onClick={() => citation && onCite?.(citation, claimFor(parts, index))}>
          {marker}
        </button>
      );
    });
  }

  const blocks = toBlocks(text);
  const caret = streaming ? <span className="caret" aria-hidden="true" /> : null;
  return (
    <div className="answer">
      {blocks.map((block, index) => {
        const last = index === blocks.length - 1;
        if (block.kind === "ul") {
          return (
            <ul key={index}>
              {block.lines.map((line, lineIndex) => (
                <li key={lineIndex}>
                  {inline(line)}
                  {last && lineIndex === block.lines.length - 1 && caret}
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={index}>
            {block.lines.map((line, lineIndex) => (
              <Fragment key={lineIndex}>
                {lineIndex > 0 && <br />}
                {inline(line)}
              </Fragment>
            ))}
            {last && caret}
          </p>
        );
      })}
      {blocks.length === 0 && caret && <p>{caret}</p>}
    </div>
  );
}
