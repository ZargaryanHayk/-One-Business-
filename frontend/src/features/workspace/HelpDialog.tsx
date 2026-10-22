import { useEffect, useRef } from "react";
import { ArrowUpRight, Check, MessageCircle, Mic, Send, X } from "lucide-react";
import { TelegramButton } from "../../components/TelegramButton";

export function HelpDialog({
  botUrl,
  onClose,
}: {
  botUrl?: string | null;
  onClose: () => void;
}) {
  const panel = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const focusable = panel.current?.querySelectorAll<HTMLElement>(
      "button, a, input, select",
    );
    focusable?.[0]?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
      if (event.key === "Tab" && focusable?.length) {
        const first = focusable[0],
          last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, []);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        ref={panel}
        className="help-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="help-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          className="icon-button close-modal"
          aria-label="Close instructions"
          onClick={onClose}
        >
          <X size={19} />
        </button>
        <span className="modal-icon">
          <Send size={27} />
        </span>
        <h2 id="help-title">Start with a message.</h2>
        <p>
          Open your Telegram bot and send what’s on your mind. Your task will
          appear here automatically.
        </p>
        <div className="help-option">
          <MessageCircle size={20} />
          <div>
            <strong>Type it out</strong>
            <p>One message becomes one task.</p>
          </div>
        </div>
        {botUrl && (
          <TelegramButton className="primary-button">
            Open Telegram <ArrowUpRight size={16} />
          </TelegramButton>
        )}
        <div className="help-option">
          <Mic size={20} />
          <div>
            <strong>Say it your way</strong>
            <p>
              Send a voice note up to 5 minutes. We’ll turn it into text and let
              you know when it’s ready.
            </p>
          </div>
        </div>
        <button className="primary-button" onClick={onClose}>
          Got it <Check size={16} />
        </button>
      </section>
    </div>
  );
}
