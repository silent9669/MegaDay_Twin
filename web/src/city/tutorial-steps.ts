import type { District } from "./world";
export const TOUR_KEY = "megaday.city.tour.v4";
export const TOUR: { place: District; title: string; text: string }[] = [
  { place: "hq", title: "Start at HQ", text: "Track a product, compare its options and review campaigns. Close the workspace to return to the city." },
  { place: "ads", title: "Choose ad platforms", text: "Review reported ad stats here. Campaigns and product decisions stay in HQ." },
  { place: "warehouse", title: "Check the warehouse", text: "Search products, inspect available units, and use Label data to assess demand." },
];
