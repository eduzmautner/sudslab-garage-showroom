import { defineConfig } from 'vite';

// Fixed, un-hashed filenames so the build can be dropped straight into a
// Shopify theme's /assets folder and referenced with `asset_url`.
export default defineConfig({
  server: { port: 5183, strictPort: true },
  build: {
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        entryFileNames: 'sudslab-showroom.js',
        chunkFileNames: 'sudslab-showroom-[name].js',
        assetFileNames: 'sudslab-showroom[extname]',
      },
    },
  },
});
