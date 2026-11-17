// Solve a reCAPTCHA v3 anchor URL with the anchorpass Node.js client.
//
// Usage, from an application where the `anchorpass` package is installed:
//   node node_solve.mjs ANCHOR_URL [PROXY_URL]
//
// PROXY_URL is any absolute http/https/socks5/socks5h URL with an explicit
// host and port; omit it for a direct route. The action defaults to the
// URL's `sa`.

import { Client, SyncClient } from 'anchorpass';

const [anchorUrl, proxy] = process.argv.slice(2);
if (!anchorUrl) {
  console.error('usage: node node_solve.mjs ANCHOR_URL [PROXY_URL]');
  process.exit(1);
}

// Asynchronous client.
const client = new Client({ proxy });
try {
  const result = await client.solve(anchorUrl); // { action: 'login' } overrides sa
  console.log(`async token: ${result.token.slice(0, 16)}…`);
} finally {
  await client.closeAsync();
}

// Synchronous client: plain threads only, never inside an async context.
const sync = new SyncClient({ proxy });
try {
  const result = sync.solveSync(anchorUrl);
  console.log(`sync  token: ${result.token.slice(0, 16)}…`);
} finally {
  await sync.closeAsync();
}

console.log('verify the token server-side in your application');
