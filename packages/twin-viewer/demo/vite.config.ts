import { defineConfig } from 'vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(dir, '../../..');

export default defineConfig({
  root: dir,
  server: {
    port: 5177,
    open: false,
    fs: {
      allow: [repoRoot],
    },
  },
  build: {
    target: 'esnext',
  },
  resolve: {
    alias: {
      '@digital-twin/contracts': path.resolve(dir, '../../contracts/src/index.ts'),
    },
  },
  assetsInclude: ['**/*.obj', '**/*.ply'],
});
