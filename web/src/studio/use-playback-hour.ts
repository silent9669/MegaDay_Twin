import { useEffect, useState } from "react";
import type { Sandbox } from "./workspace-types";
import { playbackHour } from "./replay";
export function usePlaybackHour(clock?: Sandbox | null) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!clock?.startedAt) return;
    const timer = setInterval(() => {
      const next = Date.now();
      setNow(previous => playbackHour(clock, previous) === playbackHour(clock, next) ? previous : next);
    }, 500);
    return () => clearInterval(timer);
  }, [clock]);
  return playbackHour(clock, now);
}
