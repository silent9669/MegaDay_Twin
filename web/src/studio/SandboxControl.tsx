import { useState } from "react";
import { Pause, Play, StepForward } from "lucide-react";
import { useAccountDocument } from "./account";
import { usePlaybackHour } from "./use-playback-hour";
import type { Sandbox } from "./workspace-types";
export function SandboxControl() {
  const { document, save, error: readError } = useAccountDocument<Sandbox>("sandbox");
  const [error, setError] = useState(""), [saving, setSaving] = useState(false);
  const hour = usePlaybackHour(document?.data);
  const running = !!document?.data.startedAt && hour < 24;
  async function update(nextHour: number, play = false) {
    if (!document || saving) return;
    setSaving(true);
    try {
      await save({ hour: nextHour, event: document.data.event, ...(play ? { startedAt: new Date().toISOString() } : {}) }, document.revision);
      setError("");
    } catch (error) { setError(error instanceof Error ? error.message : "Clock could not save."); }
    finally { setSaving(false); }
  }
  return <section className="studio-sandbox" aria-label="Live sandbox">
    <h2>Source playback</h2>
    <div className="sandbox-clock"><strong>{String(hour).padStart(2, "0")}:00</strong></div>
    <label className="sandbox-timeline"><span className="sr-only">Playback hour</span>
      <input aria-label="Playback hour" type="range" min="0" max="24" value={hour} disabled={saving || !document}
        onChange={event => void update(Number(event.target.value))} /><span>00:00</span><span>24:00</span>
    </label>
    <button className="studio-icon" aria-label={running ? "Pause playback" : "Start playback"} disabled={!document || saving || hour === 24}
      onClick={() => void update(hour, !running)}>{running ? <Pause size={18} /> : <Play size={18} />}</button>
    <button className="studio-icon" aria-label="Next hour" disabled={!document || saving || hour === 24} onClick={() => void update(hour + 1)}><StepForward size={18} /></button>
    {(error || readError) && <p className="studio-error" role="alert">{error || readError}</p>}
  </section>;
}
