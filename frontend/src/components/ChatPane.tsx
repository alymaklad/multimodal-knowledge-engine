import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { ChatMessage, Citation, Conversation } from "../types";
import { Answer } from "./Answer";
import { plural } from "../format";

interface Props {
  conversations: Conversation[];
  activeId: number | null;
  messages: ChatMessage[];
  busy: boolean;
  loading: boolean;
  includedCount: number;
  totalCount: number;
  readyCount: number;
  activeCitation: Citation | null;
  onSelectConversation: (id: number | null) => void;
  onAsk: (question: string) => void;
  onCite: (citation: Citation, claim: string) => void;
  onAddSources: () => void;
}

export function ChatPane(props: Props) {
  const { conversations, activeId, messages, busy, includedCount, totalCount, readyCount } = props;
  const [draft, setDraft] = useState("");
  const thread = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  // Follow the answer as it streams, unless the reader has scrolled up to read something earlier.
  useEffect(() => {
    const element = thread.current;
    if (!element) return;
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 160;
    if (nearBottom || busy) element.scrollTop = element.scrollHeight;
  }, [messages, busy]);

  useEffect(() => {
    const element = input.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 192)}px`;
  }, [draft]);

  const blocked = props.loading
    ? "Loading sources…"
    : totalCount === 0
      ? "Add a source to start asking."
      : includedCount === 0
        ? "Tick at least one source to ask about."
        : null;

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const question = draft.trim();
    if (!question || busy || blocked) return;
    props.onAsk(question);
    setDraft("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className="chat">
      <div className="chat-head">
        <label className="visually-hidden" htmlFor="conversation-picker">Conversation</label>
        <select id="conversation-picker" value={activeId ?? ""} onChange={(event) =>
          props.onSelectConversation(event.target.value ? Number(event.target.value) : null)} disabled={busy}>
          <option value="">New conversation</option>
          {conversations.map((conversation) => (
            <option key={conversation.id} value={conversation.id}>{conversation.title}</option>
          ))}
        </select>
      </div>

      <div className="thread" ref={thread} aria-live="polite">
        {messages.length === 0 ? (
          props.loading ? null : <EmptyChat totalCount={totalCount} readyCount={readyCount} onAddSources={props.onAddSources} />
        ) : (
          turns(messages).map(([question, answer]) => (
            <section className="turn" key={question?.id ?? answer?.id}>
              {question && <p className="question">{question.content}</p>}
              {answer && <AnswerTurn message={answer} question={question} activeCitation={props.activeCitation} onCite={props.onCite} />}
            </section>
          ))
        )}
      </div>

      <div className="composer">
        <form onSubmit={submit}>
          <label className="visually-hidden" htmlFor="question">Your question</label>
          <textarea id="question" ref={input} className="field" rows={1} value={draft} placeholder="Ask a question about your sources"
            onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} disabled={Boolean(blocked)} />
          <button type="submit" className="button button-primary" disabled={!draft.trim() || busy || Boolean(blocked)}>
            {busy ? "Answering…" : "Ask"}
          </button>
        </form>
        <p className="composer-hint">
          {blocked ?? `Answers come only from the ${plural(includedCount, "ticked source")}.`}
        </p>
      </div>
    </div>
  );
}

function EmptyChat({ totalCount, readyCount, onAddSources }: { totalCount: number; readyCount: number; onAddSources: () => void }) {
  if (totalCount === 0) {
    return (
      <div className="empty">
        <h2>Start with a source</h2>
        <p>Add a PDF, a web page, a YouTube video, a recording, or your own notes. Then ask about them here.</p>
        <button type="button" className="button button-primary" onClick={onAddSources}>Add sources</button>
      </div>
    );
  }
  return (
    <div className="empty">
      <h2>Ask your sources</h2>
      <p>
        Every answer is written only from the sources you've ticked, and each claim links to the passage it came from.
        {readyCount < totalCount && " Some sources are still being read, so they can't be quoted yet."}
      </p>
    </div>
  );
}

function AnswerTurn({ message, question, activeCitation, onCite }: {
  message: ChatMessage;
  question: ChatMessage | null;
  activeCitation: Citation | null;
  onCite: (citation: Citation, claim: string) => void;
}) {
  const streaming = message.status === "streaming";
  const active = activeCitation && message.citations.includes(activeCitation) ? activeCitation.marker : null;
  const rewritten =
    message.retrieval_query && question && message.retrieval_query.trim() !== question.content.trim() ? message.retrieval_query : null;

  if (message.status === "refused") {
    return <p className="answer-problem">The model declined to answer this question, so nothing was kept.</p>;
  }
  return (
    <>
      {message.content || streaming ? (
        <Answer text={message.content} citations={message.citations} streaming={streaming} activeMarker={active} onCite={onCite} />
      ) : null}
      {message.status === "failed" && (
        <p className="answer-problem">The answer stopped: {message.error_message ?? "something went wrong"}.</p>
      )}
      {message.status === "interrupted" && <p className="answer-problem">This answer was cut off before it finished.</p>}
      {rewritten && !streaming && <p className="answer-meta">Searched for “{rewritten}”</p>}
    </>
  );
}

/** Pair each question with the answer that followed it. */
function turns(messages: ChatMessage[]): [ChatMessage | null, ChatMessage | null][] {
  const pairs: [ChatMessage | null, ChatMessage | null][] = [];
  for (const message of messages) {
    if (message.role === "user") pairs.push([message, null]);
    else if (pairs.length && pairs[pairs.length - 1][1] === null) pairs[pairs.length - 1][1] = message;
    else pairs.push([null, message]);
  }
  return pairs;
}
