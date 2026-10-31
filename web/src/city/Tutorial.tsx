import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, X } from "lucide-react";
import { TOUR } from "./tutorial-steps";
export function Tutorial({
  step,
  onStep,
  onFinish,
}: {
  step: number;
  onStep: (step: number) => void;
  onFinish: () => void;
}) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [viewport, setViewport] = useState({
    w: window.innerWidth,
    h: window.innerHeight,
  });
  const previous = useRef<HTMLElement | null>(null),
    heading = useRef<HTMLHeadingElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => {
    previous.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    return () => {
      if (previous.current?.isConnected) previous.current.focus();
    };
  }, []);
  useEffect(() => {
    const update = () => {
      const target = document.querySelector<HTMLElement>(TOUR[step]!.target);
      const r = target?.getBoundingClientRect();
      setRect(r && r.width && r.height ? r : null);
      setViewport({ w: window.innerWidth, h: window.innerHeight });
    };
    update();
    heading.current?.focus();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [step]);
  const mobile = viewport.w < 700;
  const width = Math.min(348, viewport.w - 32);
  const left = mobile
    ? 16
    : Math.min(viewport.w - width - 16, Math.max(16, (rect?.right ?? 80) + 18));
  const top = mobile
    ? undefined
    : Math.max(80, Math.min(viewport.h - 270, (rect?.top ?? 100) - 12));
  return (
    <>
      {rect && (
        <div
          className="tour-target"
          aria-hidden="true"
          style={{
            left: rect.left - 4,
            top: rect.top - 4,
            width: rect.width + 8,
            height: rect.height + 8,
          }}
        />
      )}
      <motion.section
        className="city-tour guided-tour"
        role="region"
        aria-label="City tutorial"
        style={{ left, top, bottom: mobile ? 90 : undefined, width }}
        initial={{ opacity: 0, y: reduced ? 0 : 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduced ? 0 : 0.2 }}
      >
        <header>
          <span>
            {step + 1} / {TOUR.length}
          </span>
          <button aria-label="Close tutorial" onClick={onFinish}>
            <X size={18} />
          </button>
        </header>
        <h2 ref={heading} tabIndex={-1}>
          {TOUR[step]!.title}
        </h2>
        <p>{TOUR[step]!.text}</p>
        <footer>
          <button onClick={onFinish}>Skip</button>
          <button disabled={step === 0} onClick={() => onStep(step - 1)}>
            Back
          </button>
          <button
            className="primary"
            onClick={() =>
              step === TOUR.length - 1 ? onFinish() : onStep(step + 1)
            }
          >
            {step === TOUR.length - 1 ? "Done" : "Next"}
            <ArrowRight size={16} />
          </button>
        </footer>
      </motion.section>
    </>
  );
}
