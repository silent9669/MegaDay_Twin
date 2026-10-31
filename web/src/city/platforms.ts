export const PLATFORM_IDS = ["shopee", "tiktok", "meta", "google"] as const;
export type PlatformId = (typeof PLATFORM_IDS)[number];

/** Shared identity for the platform picker and the 3D district. */
export const PLATFORMS = {
  shopee: {
    name: "Shopee Ads", logo: "/brands/shopee.svg",
    primary: "#EE4D2D", accents: ["#EE4D2D", "#FFB995"],
    body: "#FFF2E8", glass: "#A9C8CA", sign: "#FFFFFF", ink: "#7F2A19",
    height: 7, signature: "Orange roof · warm storefront",
  },
  tiktok: {
    name: "TikTok Ads", logo: "/brands/tiktok.svg",
    primary: "#161823", accents: ["#25F4EE", "#FE2C55"],
    body: "#30323E", glass: "#5C7485", sign: "#161823", ink: "#FFFFFF",
    height: 6.5, signature: "Dark facade · cyan & pink trim",
  },
  meta: {
    name: "Meta Ads", logo: "/brands/meta.svg",
    primary: "#0866FF", accents: ["#0866FF", "#78BCFF"],
    body: "#EAF1FC", glass: "#83A9C9", sign: "#FFFFFF", ink: "#1C2B33",
    height: 7.5, signature: "Blue crown · light glass facade",
  },
  google: {
    name: "Google Ads", logo: "/brands/google-ads.png",
    primary: "#4285F4", accents: ["#4285F4", "#FBBC04", "#34A853"],
    body: "#FAF9F4", glass: "#ADC8D6", sign: "#FFFFFF", ink: "#3C4043",
    height: 5.5, signature: "White facade · blue, yellow & green",
  },
} satisfies Record<PlatformId, {
  name: string; logo: string; primary: string; accents: string[];
  body: string; glass: string; sign: string; ink: string;
  height: number; signature: string;
}>;
