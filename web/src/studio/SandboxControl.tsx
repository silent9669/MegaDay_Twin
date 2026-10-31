import { useCallback, useEffect, useRef, useState } from "react";
import { Pause, Play, StepForward } from "lucide-react";
import { useAccountDocument } from "./account";
import type { Sandbox } from "./workspace-types";
export function SandboxControl() {
  const {
    document,
    save,
    error: readError,
  } = useAccountDocument<Sandbox>("sandbox");
  const [running, setRunning] = useState(false),
    [error, setError] = useState(""),
    [saving, setSaving] = useState(false);
  const current = useRef(document);
  useEffect(() => {
    current.current = document;
  }, [document]);
  const update = useCallback(
    async (hour: number, event = current.current?.data.event ?? "observe") => {
      const doc = current.current;
      if (!doc) return;
      setSaving(true);
      try {
        const next = await save({ hour, event }, doc.revision);
        current.current = next;
        setError("");
        if (hour === 24) setRunning(false);
      } catch (e) {
        setRunning(false);
        setError(e instanceof Error ? e.message : "Clock could not save.");
      } finally {
        setSaving(false);
      }
    },
    [save],
  );
  useEffect(() => {
    if (!running) return;
    const interval = setInterval(() => {
      if (!window.document.hidden && current.current)
        void update(Math.min(24, current.current.data.hour + 1));
    }, 6000);
    return () => clearInterval(interval);
  }, [running, update]);
  const hour = document?.data.hour ?? 1;
  return (
    <section className="studio-sandbox" aria-label="Live sandbox">
      <div>
        <span className="eyebrow">Live sandbox</span>
        <h2>Source playback</h2>
      </div>
      <div className="sandbox-clock">
        <strong>{String(hour).padStart(2, "0")}:00</strong>
        <span>Vietnam time</span>
      </div>
      <label className="sandbox-timeline">
        <span className="sr-only">Playback hour</span>
        <input
          aria-label="Playback hour"
          type="range"
          min="0"
          max="24"
          value={hour}
          disabled={saving || !document}
          onChange={(e) => {
            setRunning(false);
            void update(Number(e.target.value));
          }}
        />
        <span>00:00</span>
        <span>24:00</span>
      </label>
      <button
        className="studio-icon"
        aria-label={running ? "Pause playback" : "Start playback"}
        disabled={!document || saving || hour === 24}
        onClick={() => setRunning(!running)}
      >
        {running ? <Pause size={18} /> : <Play size={18} />}
      </button>
      <button
        className="studio-icon"
        aria-label="Next hour"
        disabled={!document || saving || hour === 24}
        onClick={() => void update(hour + 1)}
      >
        <StepForward size={18} />
      </button>
      <p className="sandbox-note">
        Recorded observations · playback changes the view, never source stock or
        campaigns.
      </p>
      {(error || readError) && (
        <p className="studio-error" role="alert">
          {error || readError}
        </p>
      )}
    </section>
  );
}
