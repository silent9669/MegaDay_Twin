import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Tutorial } from "./Tutorial";
afterEach(() => vi.unstubAllGlobals());
it("remains usable when an anchor is missing and restores keyboard focus after Skip", () => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query) => ({
      matches: true,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  );
  const trigger = document.createElement("button");
  trigger.textContent = "Replay";
  document.body.append(trigger);
  trigger.focus();
  const finish = vi.fn(),
    next = vi.fn();
  const view = render(<Tutorial step={0} onStep={next} onFinish={finish} />);
  expect(screen.getByRole("heading", { name: "Explore Saigon" })).toHaveFocus();
  expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(next).toHaveBeenCalledWith(1);
  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  expect(finish).toHaveBeenCalledOnce();
  view.unmount();
  expect(trigger).toHaveFocus();
  trigger.remove();
});
it("offers an explicit lighting action without advancing or changing preferences automatically", () => {
  const action = vi.fn(), next = vi.fn();
  const view = render(<Tutorial step={1} onStep={next} onFinish={vi.fn()} onAction={action} />);
  expect(action).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Switch morning / night" }));
  expect(action).toHaveBeenCalledOnce();
  expect(next).not.toHaveBeenCalled();
  view.rerender(<Tutorial step={1} onStep={next} onFinish={vi.fn()} onAction={action} actionDisabled />);
  expect(screen.getByRole("button", { name: "Switch morning / night" })).toBeDisabled();
});
