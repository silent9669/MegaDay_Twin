import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { DemandDetails } from "./DemandDetails";

it("keeps the shared primary label separate from new/sparse history and the actual model", () => {
  render(<DemandDetails demand={{ primary_label: "Seasonal", source: "warehouse_ai", label_evidence: null,
    history_profile: "moi", model_selection: { primary_label: "Seasonal", model_id: "analog@1.0.0",
      from_backtest: false, policy_version: "demand-selection-v1", reasons: ["Short history; use comparable products."],
      data_status: { new: true, sparse: true, insufficient: false, history_days: 7 } } }} />);
  expect(screen.getByText("Seasonal", { exact: true })).toBeInTheDocument();
  expect(screen.getByText(/New product/)).toHaveTextContent("Sparse sales");
  expect(screen.getByText(/analog@1.0.0/)).toHaveTextContent("not validated by the label");
  expect(screen.getByText("Short history; use comparable products.")).toBeInTheDocument();
});
