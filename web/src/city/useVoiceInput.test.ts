import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useVoiceInput } from "./useVoiceInput";

class Speech {
  static latest: Speech;
  lang = "";
  continuous = false;
  interimResults = true;
  onresult: ((event: { resultIndex: number; results: { isFinal: boolean; 0: { transcript: string } }[] }) => void) | null = null;
  onerror: ((event: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn(() => this.onend?.());
  abort = vi.fn();
  constructor() { Speech.latest = this; }
}
afterEach(() => vi.unstubAllGlobals());
it("dictates final results only and stops on a context change or close", () => {
  vi.stubGlobal("SpeechRecognition", Speech);
  const transcript = vi.fn();
  const { result, rerender, unmount } = renderHook(({enabled, scope}) => useVoiceInput(enabled, scope, transcript), { initialProps: {enabled:true, scope:"A"} });
  expect(result.current.listening).toBe(false);
  act(() => result.current.toggle());
  const speech = Speech.latest;
  expect(speech.start).toHaveBeenCalledOnce();
  expect(result.current.listening).toBe(true);
  act(() => speech.onresult?.({resultIndex:0,results:[{isFinal:false,0:{transcript:"partial"}},{isFinal:true,0:{transcript:"Review inventory"}}]}));
  expect(transcript).toHaveBeenCalledExactlyOnceWith("Review inventory");
  const late = speech.onresult;
  rerender({enabled:true,scope:"B"});
  act(() => late?.({resultIndex:0,results:[{isFinal:true,0:{transcript:"Wrong product"}}]}));
  expect(transcript).toHaveBeenCalledTimes(1);
  expect(speech.abort).toHaveBeenCalledOnce();
  act(() => result.current.toggle());
  rerender({enabled:false,scope:"B"});
  expect(Speech.latest.abort).toHaveBeenCalledOnce();
  expect(result.current.listening).toBe(false);
  unmount();
});
it("reports denied microphone access and allows a retry", () => {
  vi.stubGlobal("webkitSpeechRecognition", Speech);
  const {result}=renderHook(()=>useVoiceInput(true,"A",vi.fn()));
  act(()=>result.current.toggle());
  act(()=>Speech.latest.onerror?.({error:"not-allowed"}));
  expect(result.current.error).toMatch(/denied/);
  expect(result.current.listening).toBe(false);
  act(()=>result.current.toggle());
  expect(result.current.error).toBe("");
  act(()=>result.current.toggle());
  expect(Speech.latest.stop).toHaveBeenCalledOnce();
});
it("does not start when unsupported or disabled", () => {
  const {result,unmount}=renderHook(()=>useVoiceInput(true,"A",vi.fn()));
  expect(result.current.supported).toBe(false);
  act(()=>result.current.toggle());
  expect(result.current.listening).toBe(false);
  unmount();
  vi.stubGlobal("SpeechRecognition",Speech);
  const disabled=renderHook(()=>useVoiceInput(false,"A",vi.fn()));
  act(()=>disabled.result.current.toggle());
  expect(disabled.result.current.listening).toBe(false);
});
