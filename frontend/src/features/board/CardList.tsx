import { useEffect, useRef, useState } from "react";
import type { Task } from "../../api";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { TaskCard } from "./TaskCard";

const CARDS_PER_PAGE = 25;
// Columns stack vertically at this width (see styles.css).
const STACKED_COLUMNS = "(max-width: 850px)";

// Remount (via `key`) to reset paging when the view, search, or archive changes;
// data updates never shrink the number of loaded cards.
export function CardList({
  items,
  isDueView,
  onOpen,
}: {
  items: Task[];
  isDueView: boolean;
  onOpen: (id: string) => void;
}) {
  const [visibleCount, setVisibleCount] = useState(CARDS_PER_PAGE);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  // Stacked columns would auto-load the first column forever while scrolling
  // past it, so narrow screens use the manual button only.
  const stacked = useMediaQuery(STACKED_COLUMNS);
  const hasMore = visibleCount < items.length;

  useEffect(() => {
    if (!hasMore || stacked) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setVisibleCount((prev) => prev + CARDS_PER_PAGE);
        }
      },
      { rootMargin: "250px" },
    );
    const element = sentinelRef.current;
    if (element) observer.observe(element);
    return () => observer.disconnect();
  }, [hasMore, stacked, visibleCount]);

  const displayed = items.slice(0, visibleCount);
  const remaining = items.length - displayed.length;

  return (
    <>
      {displayed.map((task) => (
        <TaskCard
          key={task.id}
          task={task}
          isDueView={isDueView}
          onOpen={() => onOpen(task.id)}
        />
      ))}
      {remaining > 0 && (
        <div ref={sentinelRef} className="lazy-cards-loader">
          <button
            type="button"
            className="lazy-load-more"
            onClick={() => setVisibleCount((prev) => prev + CARDS_PER_PAGE)}
          >
            Load more ({remaining} remaining)
          </button>
        </div>
      )}
    </>
  );
}
