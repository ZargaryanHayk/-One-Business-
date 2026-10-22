import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageCircle } from "lucide-react";
import { api, type Task, type TaskComment } from "../../api";

export function TaskComments({ task }: { task: Task }) {
  const client = useQueryClient();
  const [comment, setComment] = useState("");
  const comments = useQuery({
    queryKey: ["comments", task.id],
    queryFn: () => api<TaskComment[]>(`/tasks/${task.id}/comments`),
  });
  const post = useMutation({
    mutationFn: () =>
      api(`/tasks/${task.id}/comments`, {
        method: "POST",
        body: JSON.stringify({ text: comment }),
      }),
    onSuccess: () => {
      setComment("");
      void client.invalidateQueries({ queryKey: ["comments", task.id] });
    },
  });
  return (
    <section className="comments-section" aria-label="Task comments">
      <h3>
        <MessageCircle size={17} /> Comments
      </h3>
      {comments.isPending && <p>Loading comments…</p>}
      {comments.isError && (
        <p role="alert">
          Could not load comments.{" "}
          <button
            className="text-button"
            onClick={() => void comments.refetch()}
          >
            Try again
          </button>
        </p>
      )}
      {comments.data?.length === 0 && (
        <p className="muted">Start the conversation.</p>
      )}
      {comments.data?.map((item) => (
        <article className="comment" key={item.id}>
          <div>
            <strong>{item.author.name}</strong>
            <time dateTime={item.created_at}>
              {new Date(item.created_at).toLocaleString()}
            </time>
          </div>
          <p>{item.text}</p>
        </article>
      ))}
      {!task.archived_at && !task.project_archived && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (comment.trim()) post.mutate();
          }}
        >
          <label htmlFor="new-comment">Write a comment</label>
          <textarea
            id="new-comment"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            required
            maxLength={4000}
            rows={3}
          />
          <button
            className="primary-button"
            disabled={!comment.trim() || post.isPending}
          >
            Post comment
          </button>
        </form>
      )}
      {post.error && (
        <p className="card-error" role="alert">
          {post.error.message}
        </p>
      )}
    </section>
  );
}
