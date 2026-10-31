import { useCallback, useEffect, useRef, useState } from "react";

type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};
type VoiceWindow = Window & { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };

export function useVoiceInput(enabled: boolean, scope: string, onTranscript: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const recognition = useRef<Recognition | null>(null);
  const callback = useRef(onTranscript);
  useEffect(() => { callback.current = onTranscript; }, [onTranscript]);
  const Constructor = (window as VoiceWindow).SpeechRecognition ?? (window as VoiceWindow).webkitSpeechRecognition;
  const cancel = useCallback(() => {
    const current = recognition.current;
    recognition.current = null;
    if (current) { current.onresult = null; current.onerror = null; current.onend = null; current.abort(); }
    setListening(false);
  }, []);
  useEffect(() => cancel, [enabled, scope, cancel]);
  function toggle() {
    if (recognition.current) { recognition.current.stop(); return; }
    if (!Constructor || !enabled) return;
    const current = new Constructor();
    current.lang = navigator.language || "en-US";
    current.continuous = true;
    current.interimResults = false;
    current.onresult = event => {
      if (recognition.current !== current) return;
      const text = Array.from(event.results).slice(event.resultIndex).filter(result => result.isFinal).map(result => result[0].transcript).join(" ").trim();
      if (text) callback.current(text);
    };
    current.onerror = event => {
      if (event.error !== "aborted") setError(event.error === "not-allowed" || event.error === "service-not-allowed"
        ? "Microphone access was denied. Allow it in browser settings, or type your message."
        : event.error === "no-speech" ? "No speech detected. Try again." : "Voice input is unavailable. Try again or type your message.");
      cancel();
    };
    current.onend = () => { if (recognition.current === current) { recognition.current = null; setListening(false); } };
    recognition.current = current;
    setError("");
    try { current.start(); setListening(true); }
    catch { cancel(); setError("Could not start voice input. Check microphone access."); }
  }
  return { supported: !!Constructor, listening, error, toggle, cancel };
}
