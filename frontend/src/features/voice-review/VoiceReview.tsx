import { useEffect, useState, type FormEvent } from "react";
import { Check, LoaderCircle, PencilLine } from "lucide-react";
import "./voice-review.css";

type Transcript = {
  text: string;
  label: string;
  revision: number;
  max_length: number;
};

export function VoiceReview() {
  const [token] = useState(
    () => new URLSearchParams(window.location.hash.slice(1)).get("review") ?? "",
  );
  const [draft, setDraft] = useState<Transcript | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch("/api/voice-review", {
          headers: { "X-Voice-Edit-Token": token },
          signal: controller.signal,
        });
        const body = await response.json();
        if (!response.ok)
          throw new Error(
            body.error?.message ?? "Unable to open this transcript.",
          );
        setDraft(body);
        setText(body.text);
      } catch (failure) {
        if (!controller.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "Unable to open this transcript.",
          );
      }
    }
    void load();
    return () => controller.abort();
  }, [token]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || saving) return;
    setSaving(true);
    setError("");
    try {
      const response = await fetch("/api/voice-review", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Voice-Edit-Token": token,
        },
        body: JSON.stringify({ text, revision: draft.revision }),
      });
      const body = await response.json();
      if (!response.ok)
        throw new Error(
          body.error?.message ?? "Unable to send this correction.",
        );
      setSaved(true);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Unable to send this correction.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="voice-editor-shell">
      <section
        className="voice-editor-card"
        aria-label="Edit voice transcription"
      >
        <div className="voice-editor-mark">
          {saved ? <Check /> : <PencilLine />}
        </div>
        <p className="voice-editor-eyebrow">TASKLINE · VOICE TRANSCRIPTION</p>
        <h1>{saved ? "Correction sent" : "Make it your words"}</h1>
        {saved ? (
          <p role="status">
            Return to Telegram, review the corrected text, and tap{" "}
            <strong>Approve</strong>. You can close this editor.
          </p>
        ) : (
          <>
            <p>
              Check the transcript and type any changes. You’ll approve it in
              Telegram.
            </p>
            {draft ? (
              <form onSubmit={save}>
                <label htmlFor="voice-transcript">{draft.label}</label>
                <textarea
                  id="voice-transcript"
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  maxLength={draft.max_length}
                  rows={10}
                  autoFocus
                  required
                  disabled={saving}
                />
                <div className="voice-editor-count">
                  {text.length.toLocaleString()} /{" "}
                  {draft.max_length.toLocaleString()}
                </div>
                {error && (
                  <p className="voice-editor-error" role="alert">
                    {error}
                  </p>
                )}
                <button
                  type="submit"
                  disabled={
                    saving || !text.trim() || text.length > draft.max_length
                  }
                >
                  {saving ? (
                    <LoaderCircle className="spin" size={18} />
                  ) : (
                    <Check size={18} />
                  )}
                  {saving ? "Sending…" : "Send correction to Telegram"}
                </button>
              </form>
            ) : error ? (
              <p className="voice-editor-error" role="alert">
                {error}
              </p>
            ) : (
              <p role="status">Opening your transcript…</p>
            )}
          </>
        )}
      </section>
    </main>
  );
}
