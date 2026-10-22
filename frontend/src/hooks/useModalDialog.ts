import { useEffect, type RefObject } from "react";

/** Opens a native `<dialog>` as a modal while mounted, optionally locks page
 * scrolling, and returns focus to the previously focused element on close. */
export function useModalDialog(
  dialog: RefObject<HTMLDialogElement | null>,
  { lockScroll = true }: { lockScroll?: boolean } = {},
) {
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement as HTMLElement | null;
    element?.showModal();
    const overflow = document.body.style.overflow;
    if (lockScroll) document.body.style.overflow = "hidden";
    return () => {
      element?.close();
      if (lockScroll) document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, [dialog, lockScroll]);
}
