import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Builds a single self-contained dist/index.html: the MCP App runs in a
// sandboxed iframe (SEP-1865 / io.modelcontextprotocol/ui) that cannot rely
// on additional network requests for its own assets, so everything is
// inlined at build time.
export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    target: "es2022",
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
  },
});
