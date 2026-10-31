import type { District } from "./world";
export const TOUR_KEY = "megaday.city.tour.v2";
export const TOUR: {
  place: District | null;
  target: string;
  title: string;
  text: string;
}[] = [
  {
    place: null,
    target: '[aria-label="View city"]',
    title: "Explore Saigon",
    text: "Drag to orbit. Scroll or use + and − to zoom. District buttons work without 3D.",
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
    text: "Updates tracks changes while the app is open. Choose Explain to attach a change to a chat.",
  },
];
