import { AssistantLayout } from "./city/AssistantLayout";
import { HQRoute } from "./hq/HQRoute";
import { Navigate } from "react-router";
import type { RouteObject } from "react-router";
import { CityApp } from "./city/CityApp";
import { Login } from "./city/Login";
import { AppShell } from "./components/AppShell";


import {
  AlertsPage,
  NotFoundPage,
} from "./pages/placeholders";

// Routes follow 09-giao-dien §1. `/sku` and `/compare` without params are entry
// points for the side nav; deep links carry the SKU or campaign.
export const routes: RouteObject[] = [
 {path:'/login',element:<Login/>},
 {element:<AssistantLayout/>,children:[
  { path: "/city-demo", element: <CityApp /> },
  { path: "/mock-data", element: <CityApp studio /> },
  { path: "/sale-planner", element: <CityApp planner /> },
  { path: "/sku-assistant", element: <CityApp skuAssistant /> },
  {
    element: <AppShell />,
    children: [
      { index: true, element: <HQRoute tab="overview" /> },
      { path: "sku", element: <HQRoute tab="details" /> },
      { path: "sku/:sku", element: <HQRoute tab="details" /> },
      { path: "sku/:sku/compare", element: <HQRoute tab="comparisons" /> },
      { path: "campaign/:id/compare", element: <HQRoute tab="comparisons" /> },
      { path: "compare", element: <HQRoute tab="comparisons" /> },
      { path: "import", element: <Navigate to="/mock-data" replace /> },
      { path: "alerts", element: <AlertsPage /> },
      { path: "decisions", element: <HQRoute tab="activity" /> },
      { path: "assistant", element: <Navigate to="/city-demo?assistant=open" replace /> },
      {
        path: "settings",
        element: <Navigate to="/city-demo?panel=settings" replace />,
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
 ]},
];
