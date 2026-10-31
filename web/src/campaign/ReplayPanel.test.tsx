import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { ReplayPanel } from "./ReplayPanel";
import { requestJSON } from "../studio/account";

vi.mock("../studio/account", () => ({ requestJSON: vi.fn() }));
afterEach(() => vi.resetAllMocks());
const status = { replay_running: false, replay_hour: null, orders_until: null, ads_until: null,
  data_as_of: "2026-11-11T00:00:00+07:00", recompute_busy: false, last_error: null, finished: false };
function setup(overrides = {}) {
  vi.mocked(requestJSON).mockImplementation(async path => path.endsWith("replay-archive")
    ? { uploaded: true, canControl: true, day: "2026-11-11", adsLagHours: 2, counts: { orders: 3, inventory: 3 } }
    : { data: { ...status, ...overrides } });
  render(<MemoryRouter><ReplayPanel /></MemoryRouter>);
}
it("uploads only in Data Studio and advances one unobserved hour", async () => {
  setup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Phát thêm 1 giờ" })).toBeEnabled());
  expect(screen.getByRole("link", { name: /Tải live_11_11.zip trong Data Studio/ })).toHaveAttribute("href", "/mock-data");
  expect(screen.queryByLabelText("ZIP replay")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Phát thêm 1 giờ" }));
  await waitFor(() => expect(requestJSON).toHaveBeenCalledWith("/api/v1/live/control", {
    method: "POST", body: JSON.stringify({ command: "jump", hour: 0 }),
  }));
});
it("stops at end of day and resets only after explicit confirmation", async () => {
  setup({ replay_hour: 23, orders_until: "00:00", finished: true });
  await screen.findByText(/Đã phát hết ngày/);
  expect(screen.getByRole("button", { name: "Chạy replay" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Đặt lại replay" }));
  expect(vi.mocked(requestJSON).mock.calls.filter(c => c[1]?.method === "POST")).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Xác nhận đặt lại replay" }));
  await waitFor(() => expect(requestJSON).toHaveBeenCalledWith("/api/v1/live/control", { method: "POST", body: '{"command":"reset"}' }));
});
