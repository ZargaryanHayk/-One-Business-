import {
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api, type Status, type Task } from "./api";

export const STATUS_MUTATION_KEY = ["status"];

/** Pause periodic task refetches while a status change is in flight so a
 * stale server response cannot overwrite the optimistic update. */
export function taskRefetchInterval(client: QueryClient) {
  return () =>
    client.isMutating({ mutationKey: STATUS_MUTATION_KEY }) ? false : 30000;
}

export function useTaskStatus() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: STATUS_MUTATION_KEY,
    mutationFn: ({ id, status }: { id: string; status: Status }) =>
      api<Task>(`/tasks/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      }),
    onMutate: async ({ id, status }) => {
      await client.cancelQueries({ queryKey: ["tasks"], exact: false });
      const snapshot = client.getQueriesData<Task[]>({
        queryKey: ["tasks"],
        exact: false,
      });
      // Optimistically update all cached task lists that contain this task.
      client.setQueriesData<Task[]>(
        { queryKey: ["tasks"], exact: false },
        (old) =>
          old?.map((task) => (task.id === id ? { ...task, status } : task)),
      );
      return { snapshot };
    },
    onError: (_error, { id }, context) => {
      // Restore only this task's previous version so other in-flight
      // optimistic moves stay intact, even if the follow-up refetch fails.
      for (const [key, data] of context?.snapshot ?? []) {
        const previous = data?.find((task) => task.id === id);
        if (!previous) continue;
        client.setQueryData<Task[]>(key, (current) =>
          current?.map((task) =>
            task.id === id ? { ...task, status: previous.status } : task,
          ),
        );
      }
    },
    onSettled: () => {
      if (client.isMutating({ mutationKey: STATUS_MUTATION_KEY }) === 1) {
        void client.invalidateQueries({ queryKey: ["tasks"], exact: false });
      }
    },
  });
}
