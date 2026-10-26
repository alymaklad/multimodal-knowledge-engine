import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, startProcessing } from "../api";
import { useToast } from "../toast";

type Tab = "file" | "link" | "text";

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.mov,.webm";

interface Props {
  open: boolean;
  collectionId?: number;
  collectionName?: string;
  onClose: () => void;
  onAdded: () => void;
}

/**
 * Adds a file, a link, or pasted text. If the library already has it, the existing source is
 * added to the collection instead, since duplicates are refused across the whole library.
 */
export function AddSourceDialog({ open, collectionId, collectionName, onClose, onAdded }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("file");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  function reset() {
    setFiles([]);
    setUrl("");
    setTitle("");
    setText("");
    setError(null);
  }

  async function addOne(create: () => Promise<{ source_id: number }>): Promise<"added" | "existing"> {
    try {
      const { source_id } = await create();
      await queue(source_id);
      return "added";
    } catch (caught) {
      if (caught instanceof ApiError && caught.existingSourceId && collectionId) {
        await api.addToCollection(collectionId, [caught.existingSourceId]);
        return "existing";
      }
      throw caught;
    }
  }

  async function queue(sourceId: number) {
    try {
      await startProcessing(sourceId);
    } catch (caught) {
      // The source is saved either way; it just will not be read until processing runs.
      if (caught instanceof ApiError && caught.status === 503) {
        toast(
          "Saved, but processing isn't running. Start Redis and the worker, or set TASK_RUNNER=local and restart the API.",
          "error",
        );
        return;
      }
      throw caught;
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      let results: ("added" | "existing")[] = [];
      if (tab === "file") {
        for (const file of files) results.push(await addOne(() => api.upload(file, collectionId)));
      } else if (tab === "link") {
        results = [await addOne(() => api.addUrl(url.trim(), collectionId))];
      } else {
        results = [await addOne(() => api.addText(title.trim(), text, collectionId))];
      }
      const existing = results.filter((result) => result === "existing").length;
      const added = results.length - existing;
      toast(
        existing
          ? `${added ? `Added ${added}. ` : ""}${existing === 1 ? "One was" : `${existing} were`} already in your library, so ${existing === 1 ? "it was" : "they were"} added to this collection.`
          : added === 1
            ? "Added. It will be ready to ask about once it's read."
            : `Added ${added} sources. They'll be ready once they're read.`,
      );
      reset();
      onAdded();
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The source couldn't be added.");
    } finally {
      setBusy(false);
    }
  }

  const ready =
    tab === "file" ? files.length > 0 : tab === "link" ? url.trim().length > 0 : title.trim().length > 0 && text.trim().length > 0;

  return (
    <dialog ref={dialog} onClose={onClose} aria-labelledby="add-source-title">
      <form onSubmit={submit}>
        <div className="dialog-body">
          <h2 id="add-source-title">Add sources{collectionName ? ` to ${collectionName}` : ""}</h2>
          <div className="tabs" role="tablist">
            {(
              [
                ["file", "Upload files"],
                ["link", "Add a link"],
                ["text", "Paste text"],
              ] as const
            ).map(([value, label]) => (
              <button key={value} type="button" role="tab" className="tab" aria-selected={tab === value}
                onClick={() => { setTab(value); setError(null); }}>
                {label}
              </button>
            ))}
          </div>

          {tab === "file" && (
            <div className="stack">
              <label
                className={`dropzone${dragging ? " dragging" : ""}`}
                onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  setFiles(Array.from(event.dataTransfer.files));
                }}
              >
                <strong>{files.length ? files.map((file) => file.name).join(", ") : "Drop files here, or choose them"}</strong>
                <span className="muted small">PDF, Word, text and Markdown, images, audio, and video</span>
                <input ref={fileInput} className="visually-hidden" type="file" multiple accept={ACCEPT}
                  onChange={(event) => setFiles(Array.from(event.target.files ?? []))} />
              </label>
            </div>
          )}

          {tab === "link" && (
            <div className="stack">
              <label className="stack" style={{ gap: "0.35rem" }}>
                <span>Web page or YouTube video</span>
                <input className="field" type="url" required placeholder="https://" value={url}
                  onChange={(event) => setUrl(event.target.value)} autoFocus />
              </label>
              <p className="muted small" style={{ margin: 0 }}>
                Pages are fetched as they are now. For YouTube, the video's captions are used.
              </p>
            </div>
          )}

          {tab === "text" && (
            <div className="stack">
              <label className="stack" style={{ gap: "0.35rem" }}>
                <span>Title</span>
                <input className="field" required value={title} onChange={(event) => setTitle(event.target.value)} autoFocus />
              </label>
              <label className="stack" style={{ gap: "0.35rem" }}>
                <span>Text</span>
                <textarea className="field" required rows={8} value={text} onChange={(event) => setText(event.target.value)} />
              </label>
            </div>
          )}

          {error && <p className="notice error-text" role="alert">{error}</p>}
        </div>
        <div className="dialog-foot">
          <button type="button" className="button" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="button button-primary" disabled={!ready || busy}>
            {busy ? "Adding…" : "Add"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
