import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Project } from "../../api";

export function ProjectCreator({
  onCreated,
  onClose,
}: {
  onCreated: (project: Project) => void;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ name }),
      }),
    onSuccess: (project) => {
      void client.invalidateQueries({ queryKey: ["projects"] });
      onCreated(project);
    },
  });
  return (
    <form
      className="task-composer project-creator"
      aria-label="New project"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <label htmlFor="project-name">Project name</label>
      <input
        id="project-name"
        value={name}
        onChange={(event) => setName(event.target.value)}
        required
        maxLength={120}
        autoFocus
      />
      <div className="composer-actions">
        <button className="text-button" type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          className="primary-button"
          disabled={!name.trim() || mutation.isPending}
        >
          Create project
        </button>
      </div>
      {mutation.error && (
        <p role="alert" className="card-error">
          {mutation.error.message}
        </p>
      )}
    </form>
  );
}
