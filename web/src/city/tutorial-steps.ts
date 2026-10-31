import type { District } from "./world";
export const TOUR_KEY = "megaday.city.tour.v4";
export const TOUR: { place: District; title: string; text: string }[] = [
  { place: "hq", title: "Start at HQ", text: "Review the overview, then open Comparisons to inspect campaign results." },
  { place: "ads", title: "Choose ad platforms", text: "Add or remove platform buildings here. Campaign data stays unchanged." },
  { place: "warehouse", title: "Check the warehouse", text: "Search products, inspect available units, and use Label data to assess demand." },
];
