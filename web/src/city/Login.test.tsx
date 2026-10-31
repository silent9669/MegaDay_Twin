import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { Login } from "./Login";
import { requestJSON } from "../studio/account";

vi.mock("../studio/account", () => ({ requestJSON: vi.fn() }));
beforeEach(() => {
  vi.mocked(requestJSON).mockReset();
});

it("shows the password only when requested and announces its state", async () => {
  render(<Login />);
  const password = screen.getByLabelText("Password", { exact: true });
  expect(password).toHaveAttribute("type", "password");
  await userEvent.click(screen.getByRole("button", { name: "Show password" }));
  expect(password).toHaveAttribute("type", "text");
  expect(screen.getByRole("button", { name: "Hide password" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(screen.getByRole("button", { name: "Hide password" }));
  expect(password).toHaveAttribute("type", "password");
});

it("keeps the submitted credentials stable while signing in", async () => {
  vi.mocked(requestJSON).mockReturnValue(new Promise(() => {}));
  render(<Login />);
  await userEvent.type(screen.getByLabelText("Username"), "judges");
  await userEvent.type(screen.getByLabelText("Password", { exact: true }), "test-password");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(screen.getByRole("button", { name: "Signing in…" })).toBeDisabled();
  expect(screen.getByLabelText("Username")).toHaveAttribute("readonly");
  expect(screen.getByLabelText("Password", { exact: true })).toHaveAttribute("readonly");
  expect(screen.getByRole("form", { name: "Sign in" })).toHaveAttribute("aria-busy", "true");
});

it("announces a failed login and keeps the fields available for correction", async () => {
  vi.mocked(requestJSON).mockRejectedValue(new Error("Username or password is incorrect."));
  render(<Login />);
  await userEvent.type(screen.getByLabelText("Username"), "judges");
  await userEvent.type(screen.getByLabelText("Password", { exact: true }), "wrong-password");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Username or password is incorrect.");
  expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  expect(screen.getByLabelText("Username")).toHaveValue("judges");
  expect(screen.getByLabelText("Password", { exact: true })).not.toHaveAttribute("readonly");
});
