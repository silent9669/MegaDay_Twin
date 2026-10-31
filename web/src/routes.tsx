import { Navigate } from "react-router";
import type { RouteObject } from "react-router";
import { CityApp } from "./city/CityApp";
import { Login } from "./city/Login";
import { AppShell } from "./components/AppShell";
import { CompareIndexPage, ComparePage } from "./pages/compare/ComparePage";
import { ShiftOverviewPage } from "./pages/ShiftOverviewPage";
import {
  AlertsPage,
  AssistantPage,
  DecisionsPage,
  NotFoundPage,
  SkuDetailPage,
} from "./pages/placeholders";

// Routes follow 09-giao-dien §1. `/sku` and `/compare` without params are entry
// points for the side nav; deep links carry the SKU or campaign.
export const routes: RouteObject[] = [
  { path: "/city-demo", element: <CityApp /> },
  { path: "/mock-data", element: <CityApp studio /> },
  { path: "/sale-planner", element: <CityApp planner /> },
  { path: "/sku-assistant", element: <CityApp skuAssistant /> },
  { path: "/login", element: <Login /> },
  {
    element: <AppShell />,
    children: [
      { index: true, element: <ShiftOverviewPage /> },
      { path: "sku", element: <SkuDetailPage /> },
      { path: "sku/:sku", element: <SkuDetailPage /> },
      { path: "sku/:sku/compare", element: <ComparePage /> },
      { path: "campaign/:id/compare", element: <ComparePage /> },
      { path: "compare", element: <CompareIndexPage /> },
      { path: "import", element: <Navigate to="/mock-data" replace /> },
      { path: "alerts", element: <AlertsPage /> },
      { path: "decisions", element: <DecisionsPage /> },
      { path: "assistant", element: <AssistantPage /> },
      {
        path: "settings",
        element: <Navigate to="/city-demo?panel=settings" replace />,
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
