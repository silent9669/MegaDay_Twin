import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HQWorkspace } from "./HQWorkspace";
import { defaultHQ, type HQDocument } from "./types";
import type { Doc } from "../studio/workspace-types";
import type { SourcePack } from "../studio/sources";
let saved: Doc<HQDocument>, rejectSave: boolean;
const pack: SourcePack = {
  schema: "hackathon.sources.v1",
  files: [
    {
      kind: "inventory",
      name: "stock.csv",
      origin: "upload",
      hash: "abc",
      text: "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Product A,83\nB,Product B,20",
    },
  ],
};
beforeEach(() => {
  saved = {
    revision: 0,
    updatedAt: "2026-10-31T00:00:00Z",
    data: structuredClone(defaultHQ),
  };
  rejectSave = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      expect(path).toBe("/api/workspace/hq");
      if (init?.method === "PUT") {
        if (rejectSave)
          return Response.json(
            { error: { message: "Save failed" } },
            { status: 503 },
          );
        const body = JSON.parse(String(init.body));
        expect(body.revision).toBe(saved.revision);
        saved = { ...saved, data: body.data, revision: saved.revision + 1 };
      }
      return Response.json(saved);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());
async function mount() {
  render(
    <MemoryRouter>
      <HQWorkspace pack={pack} revision={3} />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(
      screen.getAllByRole("button", { name: "Track product" })[1],
    ).toBeEnabled(),
  );
}
it("tracks two products, keeps one active, and isolates unsaved worksheet drafts across tabs", async () => {
  await mount();
  fireEvent.click(
    within(screen.getByText("Product A").closest("tr")!).getByRole("button", {
      name: "Track product",
    }),
  );
  await screen.findByRole("heading", { name: "Product A", level: 2 });
  expect(saved.data.active).toBe("workspace:A");
  fireEvent.click(screen.getByRole("tab", { name: "Comparisons" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Load reference example" }),
  );
  expect(screen.getByText("2,535,000 VND")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "SKU Details" }));
  fireEvent.click(screen.getByRole("tab", { name: "Comparisons" }));
  expect(screen.getByLabelText("Available units")).toHaveValue(83);
  fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
  fireEvent.click(
    within(screen.getByText("Product B").closest("tr")!).getByRole("button", {
      name: "Compare",
    }),
  );
  await screen.findByRole("heading", { name: "Product B", level: 2 });
  expect(screen.getByLabelText("Available units")).toHaveValue(null);
  expect(saved.data.tracked).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Select A" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Available units")).toHaveValue(83),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save assumptions" }));
  await waitFor(() =>
    expect(saved.data.worksheets["workspace:A"]?.stock).toBe(83),
  );
});
it("retains a note on save failure and stores activity only after success", async () => {
  await mount();
  fireEvent.click(
    within(screen.getByText("Product A").closest("tr")!).getByRole("button", {
      name: "Track product",
    }),
  );
  await screen.findByRole("heading", { name: "Product A", level: 2 });
  fireEvent.click(screen.getByRole("tab", { name: "Activity Logs" }));
  fireEvent.change(screen.getByLabelText("Add a note"), {
    target: { value: "Review campaign costs" },
  });
  rejectSave = true;
  fireEvent.click(screen.getByRole("button", { name: "Save note" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Add a note")).toHaveValue(
    "Review campaign costs",
  );
  rejectSave = false;
  fireEvent.click(screen.getByRole("button", { name: "Save note" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Add a note")).toHaveValue(""),
  );
  expect(saved.data.activity[0]).toMatchObject({
    product: "workspace:A",
    action: "Note",
    detail: "Review campaign costs",
  });
});
