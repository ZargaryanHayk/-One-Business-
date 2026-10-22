import { Circle, CircleCheck, Clock3 } from "lucide-react";
import type { Status } from "../../api";

export const columns: {
  status: Status;
  label: string;
  hint: string;
  icon: typeof Circle;
}[] = [
  {
    status: "pending",
    label: "Pending",
    hint: "A little possibility starts here.",
    icon: Circle,
  },
  {
    status: "in_progress",
    label: "In progress",
    hint: "One step closer to done.",
    icon: Clock3,
  },
  {
    status: "completed",
    label: "Completed",
    hint: "Make room for a small celebration.",
    icon: CircleCheck,
  },
];
