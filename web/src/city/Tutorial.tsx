import { useEffect, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, X } from "lucide-react";
import { TOUR } from "./tutorial-steps";
export function Tutorial({ step, onStep, onFinish }: {
  step: number; onStep: (step: number) => void; onFinish: () => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const reduced = useReducedMotion();
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [step]);
  return (
    <motion.section className="workspace-tour" aria-label="City tutorial"
      initial={{ opacity: 0, y: reduced ? 0 : 6 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduced ? 0 : 0.18 }}>
      <header>
        <span>{step + 1} / {TOUR.length}</span>
        <button aria-label="Close tutorial" onClick={onFinish}><X size={16} /></button>
      </header>
      <h2 ref={heading} tabIndex={-1}>{TOUR[step]!.title}</h2>
      <p>{TOUR[step]!.text}</p>
      <footer>
        <button onClick={onFinish}>Skip</button>
        <button disabled={step === 0} onClick={() => onStep(step - 1)}>Back</button>
        <button className="primary" onClick={() => step === TOUR.length - 1 ? onFinish() : onStep(step + 1)}>
          {step === TOUR.length - 1 ? "Done" : "Next"}<ArrowRight size={16} />
        </button>
      </footer>
    </motion.section>
  );
}
