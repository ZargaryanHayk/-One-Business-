import { CheckCheck } from "lucide-react";

/** The Taskline wordmark, linking home. */
export function Brand({ label }: { label?: string }) {
  return (
    <a className="brand" href="/" aria-label={label}>
      <span className="brand-mark">
        <CheckCheck size={23} />
      </span>
      Taskline<span className="brand-dot">.</span>
    </a>
  );
}
