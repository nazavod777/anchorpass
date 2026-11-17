import { writeFile } from 'node:fs/promises';
await writeFile(new URL('../dist/index.mjs', import.meta.url), "import api from './index.js';\nexport const { Client, solve, SyncClient, solveSync, loadBg, loadBgSync, extractAction, AnchorpassError } = api;\n");
