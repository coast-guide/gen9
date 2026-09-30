"use client";

import { useCallback } from "react";

type Edge = "top" | "bottom";
const pinned = new Map<Element, { edge: Edge; height: number }>();

// Only what is pinned now: on a short viewport those regions scroll with the page (globals.css)
const isPinned = (element: Element) => /sticky|fixed/.test(getComputedStyle(element).position);

function apply() {
  const sum = (edge: Edge) =>
    [...pinned].reduce((total, [element, p]) => total + (p.edge === edge && isPinned(element) ? p.height : 0), 0);
  const root = document.documentElement.style;
  root.setProperty("--sticky-top", `${sum("top")}px`);
  root.setProperty("--sticky-bottom", `${sum("bottom")}px`);
}

/**
 * A region pinned over the page (the phones' top bar, a chat's title, its composer): its height is
 * kept in --sticky-top or --sticky-bottom, which html's scroll-padding reads (globals.css), so a
 * control that takes focus scrolls clear of it instead of under it (WCAG 2.2, 2.4.11 Focus Not
 * Obscured; W3C technique C43; manual-e2e.md, P3-D10). Hidden, it measures 0. Returns the ref.
 */
// A zoom or a turned phone can pin or unpin them without resizing them
let watching = false;
function watchViewport() {
  if (watching) return;
  watching = true;
  window.addEventListener("resize", apply);
}

export function useStickyInset(edge: Edge) {
  // A callback ref, so a region that appears later (a new chat's title, once it has an id) counts
  // too; React 19 runs the cleanup it returns when the element goes
  return useCallback(
    (element: HTMLElement | null) => {
      if (!element) return;
      watchViewport();
      const observer = new ResizeObserver(([entry]) => {
        pinned.set(element, { edge, height: entry.borderBoxSize[0]?.blockSize ?? element.offsetHeight });
        apply();
      });
      observer.observe(element);
      return () => {
        observer.disconnect();
        pinned.delete(element);
        apply();
      };
    },
    [edge],
  );
}
