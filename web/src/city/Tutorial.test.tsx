import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Tutorial } from "./Tutorial";
import { TOUR } from "./tutorial-steps";
it("guides HQ, ads and warehouse in three steps without leaving the working panel", () => {
  expect(TOUR.map(step => step.place)).toEqual(["hq", "ads", "warehouse"]);
  const finish = vi.fn(), next = vi.fn();
  const view = render(<Tutorial step={0} onStep={next} onFinish={finish} />);
  expect(screen.getByRole("heading", { name: "Start at HQ" })).toHaveFocus();
  expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(next).toHaveBeenCalledWith(1);
  view.rerender(<Tutorial step={2} onStep={next} onFinish={finish} />);
  expect(screen.getByRole("heading", { name: "Check the warehouse" })).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(finish).toHaveBeenCalledOnce();
});
