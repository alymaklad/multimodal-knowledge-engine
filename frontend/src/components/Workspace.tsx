import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, streamAnswer } from "../api";
import { isWorking } from "../format";
import { useToast } from "../toast";
import type { ChatMessage, Citation, Collection, Conversation, Source } from "../types";
import { AddSourceDialog } from "./AddSourceDialog";
import { ChatPane } from "./ChatPane";
import { PassagePane, type Selection } from "./PassagePane";
import { SourcesPane } from "./SourcesPane";

type MobilePane = "sources" | "chat" | "passage";
const POLL_MS = 3000;

export function Workspace({ collection, onCollectionsChanged }: { collection: Collection; onCollectionsChanged: () => void }) {
  const toast = useToast();
  const [sources, setSources] = useState<Source[]>([]);
  const [loadingSources, setLoadingSources] = useState(true);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // null means every source in the collection, including ones added later.
  const [scope, setScope] = useState<number[] | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [mobilePane, setMobilePane] = useState<MobilePane>("chat");
  const abort = useRef<AbortController | null>(null);

  const loadSources = useCallback(async () => {
    try {
      setSources(await api.sources(collection.id));
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "Sources couldn't be loaded.", "error");
    } finally {
      setLoadingSources(false);
    }
  }, [collection.id, toast]);

  const openConversation = useCallback(async (id: number | null) => {
    setSelection(null);
    if (id === null) {
      setActiveId(null);
      setMessages([]);
      setScope(null);
      return;
    }
    const detail = await api.conversation(id);
    setActiveId(detail.id);
    setMessages(detail.messages);
    setScope(detail.source_ids);
  }, []);

  // A different collection is a fresh workspace.
  useEffect(() => {
    abort.current?.abort();
    setSources([]);
    setLoadingSources(true);
    setSelection(null);
    setMessages([]);
    setActiveId(null);
    setScope(null);
    void loadSources();
    api
      .conversations(collection.id)
      .then((list) => {
        setConversations(list);
        if (list.length) void openConversation(list[0].id);
      })
      .catch(() => setConversations([]));
    return () => abort.current?.abort();
  }, [collection.id, loadSources, openConversation]);

  // Keep checking while anything is still being fetched or read.
  const working = sources.some(isWorking);
  useEffect(() => {
    if (!working) return;
    const timer = window.setInterval(() => void loadSources(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [working, loadSources]);

  // Keep an open source's details current as it finishes processing.
  useEffect(() => {
    if (selection?.type !== "source") return;
    const fresh = sources.find((source) => source.id === selection.source.id);
    if (fresh && fresh.updated_at !== selection.source.updated_at) setSelection({ type: "source", source: fresh });
  }, [sources, selection]);

  const included = useMemo(() => {
    const ids = sources.map((source) => source.id);
    return new Set(scope === null ? ids : scope.filter((id) => ids.includes(id)));
  }, [sources, scope]);

  function updateScope(next: Set<number>) {
    const all = sources.length > 0 && sources.every((source) => next.has(source.id));
    const value = all ? null : Array.from(next);
    setScope(value);
    // An empty selection stays local: a conversation must always search something.
    if (activeId !== null && (value === null || value.length > 0)) {
      api.setConversationScope(activeId, value).catch((caught) =>
        toast(caught instanceof Error ? caught.message : "The selection couldn't be saved.", "error"),
      );
    }
  }

  function toggle(sourceId: number) {
    const next = new Set(included);
    if (next.has(sourceId)) next.delete(sourceId);
    else next.add(sourceId);
    updateScope(next);
  }

  async function ask(question: string) {
    setBusy(true);
    setSelection(null);
    setMobilePane("chat");
    const controller = new AbortController();
    abort.current = controller;
    const pendingId = -Date.now();
    try {
      let conversationId = activeId;
      if (conversationId === null) {
        const created = await api.createConversation(collection.id, scope);
        conversationId = created.id;
        setActiveId(created.id);
        setConversations((list) => [created, ...list]);
      }
      // Show the question at once; the stored copy replaces it when the server confirms it.
      setMessages((list) => [...list, placeholder(pendingId, conversationId!, "user", question)]);
      await streamAnswer(
        conversationId,
        question,
        (event) => {
          if (event.type === "user_message") {
            setMessages((list) => [
              ...list.filter((message) => message.id !== pendingId),
              event.data,
              placeholder(pendingId - 1, conversationId!, "assistant", ""),
            ]);
          } else if (event.type === "retrieval") {
            setMessages((list) =>
              list.map((message) => (message.id === pendingId - 1 ? { ...message, retrieval_query: event.data.query } : message)),
            );
          } else if (event.type === "delta") {
            setMessages((list) =>
              list.map((message) =>
                message.id === pendingId - 1 ? { ...message, content: message.content + event.data.text } : message,
              ),
            );
          } else if (event.type === "done") {
            setMessages((list) => list.map((message) => (message.id === pendingId - 1 ? event.data : message)));
          } else if (event.type === "error") {
            setMessages((list) =>
              list.map((message) =>
                message.id === pendingId - 1
                  ? { ...message, status: event.data.status === "refused" ? "refused" : "failed", error_message: event.data.message }
                  : message,
              ),
            );
          }
        },
        controller.signal,
      );
      const refreshed = await api.conversations(collection.id);
      setConversations(refreshed);
    } catch (caught) {
      if (controller.signal.aborted) return;
      setMessages((list) => list.filter((message) => message.id !== pendingId && message.id !== pendingId - 1));
      toast(caught instanceof ApiError ? caught.message : "The question couldn't be sent.", "error");
    } finally {
      if (abort.current === controller) abort.current = null;
      setBusy(false);
    }
  }

  function openCitation(citation: Citation, claim: string) {
    setSelection({ type: "citation", citation, claim });
    setMobilePane("passage");
  }

  function openSource(source: Source) {
    setSelection({ type: "source", source });
    setMobilePane("passage");
  }

  const readyCount = sources.filter((source) => source.status === "completed" || source.status === "needs_review").length;
  const activeCitation = selection?.type === "citation" ? selection.citation : null;

  return (
    <>
      <main className={`workspace${selection ? "" : " no-passage"}`}>
        <aside className={`pane${mobilePane === "sources" ? " is-active" : ""}`} aria-label="Sources">
          <SourcesPane
            sources={sources}
            included={included}
            loading={loadingSources}
            openSourceId={selection?.type === "source" ? selection.source.id : null}
            onToggle={toggle}
            onIncludeAll={() => updateScope(new Set(sources.map((source) => source.id)))}
            onOpen={openSource}
            onAdd={() => setAdding(true)}
          />
        </aside>
        <section className={`pane${mobilePane === "chat" ? " is-active" : ""}`} aria-label="Conversation">
          <ChatPane
            conversations={conversations}
            activeId={activeId}
            messages={messages}
            busy={busy}
            loading={loadingSources}
            includedCount={included.size}
            totalCount={sources.length}
            readyCount={readyCount}
            activeCitation={activeCitation}
            onSelectConversation={(id) => void openConversation(id)}
            onAsk={(question) => void ask(question)}
            onCite={openCitation}
            onAddSources={() => setAdding(true)}
          />
        </section>
        {selection && (
          <aside className={`pane${mobilePane === "passage" ? " is-active" : ""}`} aria-label="Passage">
            <PassagePane
              selection={selection}
              sources={sources}
              collectionId={collection.id}
              onOpenSource={openSource}
              onClose={() => {
                setSelection(null);
                setMobilePane("chat");
              }}
              onChanged={() => {
                void loadSources();
                onCollectionsChanged();
              }}
            />
          </aside>
        )}
        <nav className="mobile-tabs" aria-label="Panes">
          <button type="button" aria-pressed={mobilePane === "sources"} onClick={() => setMobilePane("sources")}>Sources</button>
          <button type="button" aria-pressed={mobilePane === "chat"} onClick={() => setMobilePane("chat")}>Chat</button>
          {selection && (
            <button type="button" aria-pressed={mobilePane === "passage"} onClick={() => setMobilePane("passage")}>
              {selection.type === "citation" ? "Passage" : "Source"}
            </button>
          )}
        </nav>
      </main>
      <AddSourceDialog
        open={adding}
        collectionId={collection.id}
        collectionName={collection.name}
        onClose={() => setAdding(false)}
        onAdded={() => {
          void loadSources();
          onCollectionsChanged();
        }}
      />
    </>
  );
}

function placeholder(id: number, conversationId: number, role: ChatMessage["role"], content: string): ChatMessage {
  return {
    id,
    conversation_id: conversationId,
    role,
    content,
    status: role === "assistant" ? "streaming" : "complete",
    grounded: false,
    citations: [],
    retrieval_query: null,
    model: null,
    error_message: null,
    created_at: new Date().toISOString(),
  };
}
