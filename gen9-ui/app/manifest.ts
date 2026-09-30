import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Gen9",
    short_name: "Gen9",
    description: "Get any task done with autonomous agents.",
    start_url: "/chat",
    display: "standalone",
    background_color: "#F6F7F9",
    theme_color: "#3446E0",
    icons: [
      { src: "/brand/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/brand/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/brand/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
