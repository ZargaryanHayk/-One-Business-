import { useEffect, useRef } from "react";
import { ChevronDown, Users } from "lucide-react";
import type { User } from "../api";

export function AssigneeSelector({
  people,
  value,
  onChange,
  label,
  loading = false,
  disabled = false,
  error,
}: {
  people: User[];
  value: string[];
  onChange: (value: string[]) => void;
  label: string;
  loading?: boolean;
  disabled?: boolean;
  error?: string;
}) {
  const root = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node))
        root.current?.removeAttribute("open");
    };
    document.addEventListener("click", close, true);
    return () => document.removeEventListener("click", close, true);
  }, []);
  const names = people
    .filter((person) => value.includes(person.id))
    .map((person) => person.name);
  return (
    <div className="assignee-field">
      <span>Assigned to</span>
      <details
        ref={root}
        className="assignee-selector"
        onKeyDown={(event) => {
          if (event.key === "Escape" && root.current?.open) {
            event.stopPropagation();
            root.current.open = false;
            root.current.querySelector("summary")?.focus();
          }
        }}
      >
        <summary aria-label={label}>
          <Users size={15} />
          <span>{names.length ? names.join(", ") : "Unassigned"}</span>
          <ChevronDown size={15} />
        </summary>
        <div
          className="assignee-options"
          role="group"
          aria-label="Project assignees"
        >
          <p>Select one or more people</p>
          {loading && <p role="status">Loading people…</p>}
          {error && <p role="alert">{error}</p>}
          {!loading && !error && !people.length && (
            <p>No project members available.</p>
          )}
          <div className="assignee-list">
            {people.map((person) => (
              <label key={person.id} className="assignee-option">
                <input
                  type="checkbox"
                  checked={value.includes(person.id)}
                  disabled={disabled || loading}
                  onChange={(event) =>
                    onChange(
                      event.target.checked
                        ? [...value, person.id]
                        : value.filter((id) => id !== person.id),
                    )
                  }
                />
                <span>
                  {person.name}
                  <small>{person.email}</small>
                </span>
              </label>
            ))}
          </div>
          <div className="assignee-actions">
            <button
              type="button"
              disabled={disabled || !value.length}
              onClick={() => onChange([])}
            >
              Clear selection
            </button>
            <button
              type="button"
              onClick={() => {
                root.current?.removeAttribute("open");
                root.current?.querySelector("summary")?.focus();
              }}
            >
              Done
            </button>
          </div>
        </div>
      </details>
    </div>
  );
}
