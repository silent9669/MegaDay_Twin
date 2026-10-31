import { useEffect, useRef, useState, type PointerEvent, type KeyboardEvent } from "react";
export function usePanelResize(preferredWidth: number, minimumWidth: number) {
  const [viewport, setViewport] = useState(window.innerWidth);
  const [width, setWidth] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);
  const drag = useRef<{ x: number; width: number } | null>(null);
  useEffect(() => {
    const resize = () => setViewport(window.innerWidth);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const maximum = Math.max(minimumWidth, viewport - 112);
  const clamp = (value: number) => Math.min(maximum, Math.max(minimumWidth, value));
  const currentWidth = expanded ? maximum : clamp(width ?? preferredWidth);
  return {
    expanded, setExpanded,
    style: viewport >= 900 ? { width: currentWidth, left: "auto" } : undefined,
    grip: {
      role: "separator" as const, tabIndex: 0,
      "aria-orientation": "vertical" as const,
      "aria-label": "Resize panel",
      "aria-valuemin": minimumWidth, "aria-valuemax": maximum,
      "aria-valuenow": Math.round(currentWidth),
      onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
        event.preventDefault(); event.stopPropagation();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { x: event.clientX, width: currentWidth };
      },
      onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
        if (!drag.current) return;
        setExpanded(false);
        setWidth(clamp(drag.current.width + drag.current.x - event.clientX));
      },
      onPointerUp: () => { drag.current = null; },
      onLostPointerCapture: () => { drag.current = null; },
      onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
        const next = event.key === "ArrowLeft" ? currentWidth + 32 : event.key === "ArrowRight" ? currentWidth - 32 : event.key === "Home" ? minimumWidth : event.key === "End" ? maximum : null;
        if (next === null) return;
        event.preventDefault(); setExpanded(false); setWidth(clamp(next));
      },
    },
  };
}
