import { readdir, readFile } from 'node:fs/promises';
let failed = false;
for (const name of await readdir(new URL('../src', import.meta.url))) {
  if (!name.endsWith('.ts')) continue;
  const source = await readFile(new URL(`../src/${name}`, import.meta.url), 'utf8');
  if (/\b(?:console\.log|debugger|TODO)\b|[\t\r]/.test(source) || / +$/m.test(source)) {
    process.stderr.write(`Source integrity failed: ${name}\n`); failed = true;
  }
}
if (failed) process.exitCode = 1;
else process.stdout.write('Source integrity passed\n');
