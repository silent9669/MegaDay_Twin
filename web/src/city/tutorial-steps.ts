import type { District } from "./world";
export const TOUR_KEY = "megaday.city.tour.v3";
export const TOUR: {
  place: District | null;
  target: string;
  title: string;
  text: string;
  action?: "river" | "appearance";
}[] = [
  {
    place: null,
    target: '[aria-label="View city"]',
    title: "Explore Saigon",
    action: "river",
    text: "Tap a floating label to fly there. Drag to orbit; pinch or use + and − to zoom. Explore opens the river, Bitexco and Landmark 81.",
  },
  {
    place: null,
    target: '[aria-label="Night mode"]',
    title: "Morning to night",
    action: "appearance",
    text: "Try the sun button: the city lights and interface change together. Your account remembers this view; source time stays unchanged.",
  },
  {
    place: "warehouse",
    target: '[aria-label="Warehouse"]',
    title: "Check inventory",
    text: "Open Warehouse, then Open workspace to inspect available units and source timestamps.",
  },
  {
    place: "ads",
    target: '[aria-label="Ads Platform"]',
    title: "Inspect ad platforms",
    text: "Open workspace to add platform buildings. This changes the city view, not campaigns.",
  },
  {
    place: "hq",
    target: '[aria-label="MegaDay HQ"]',
    title: "Visit MegaDay Twin",
    text: "Open HQ for the overview and campaign comparisons.",
  },
  {
    place: null,
    target: '[aria-label="Data Studio"]',
    title: "Review your sources",
    text: "Data Studio holds imports, source checks, and the shared simulation clock.",
  },
  {
    place: null,
    target: '.city-nav-tools [aria-label="Open PM assistant"]',
    title: "Meet Milo",
    text: "Milo speaks when important data changes. Review opens Updates; Explain attaches the change to your draft. Send asks Milo to explain it.",
  },
];
