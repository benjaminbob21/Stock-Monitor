import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Stock-Monitor",
    short_name: "Stocks",
    description: "Explainable, human-in-the-loop stock conviction scoring.",
    start_url: "/",
    display: "standalone",
    background_color: "#0b1020",
    theme_color: "#0b1020",
    orientation: "portrait",
    icons: [
      {
        src: "/icons/icon.svg",
        sizes: "any",
        type: "image/svg+xml",
        purpose: "any",
      },
      {
        src: "/icons/icon.svg",
        sizes: "any",
        type: "image/svg+xml",
        purpose: "maskable",
      },
    ],
  };
}
