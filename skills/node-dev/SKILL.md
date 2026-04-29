# Node.js & JavaScript Development — Skill

**Trigger phrases**: node, npm, npx, pnpm, yarn, bun, package.json,
package-lock, pnpm-lock, yarn.lock, vite, webpack, turbopack, esbuild, rollup,
parcel, next, next.js, nuxt, react, vue, svelte, sveltekit, astro, remix,
typescript, tsc, ts-node, tsx, eslint, prettier, biome, jest, vitest,
playwright, cypress, puppeteer, express, fastify, hono, nestjs, graphql,
apollo, prisma, drizzle, knex, mongoose, build error, type error, MODULE_NOT_FOUND,
ENOENT, EACCES, deploy node app, vercel, netlify, cloudflare workers, pm2,
forever, nodemon, monorepo, turborepo, nx, lerna, workspaces.

Use when the user asks to write, run, debug, build, lint, or deploy
Node/TypeScript code.

---

## Operating principles

- **Detect the package manager.** `pnpm-lock.yaml` → pnpm. `yarn.lock` →
  yarn. `bun.lockb` → bun. Else → npm. Match what exists; don't switch.
- **Match the Node version.** Check `.nvmrc`, `package.json#engines`, or
  `volta` config. Use that exact version (`fnm`, `nvm`, or `volta`).
- **Run scripts via the package manager.** `pnpm dev` not `node ...`
  unless explicitly asked.
- **TypeScript first.** If `tsconfig.json` exists, run `tsc --noEmit` to
  catch type errors before declaring done.
- **Don't commit lockfile changes from a different package manager.**
- **Avoid global installs.** Use `npx` / `pnpm dlx` / `bunx` instead.

---

## Toolbox

```bash
which node npm pnpm yarn bun npx pnpx
node -v && npm -v
```

If Node is missing or wrong version:

```bash
# install fnm (fast node version manager)
curl -fsSL https://fnm.vercel.app/install | bash
exec $SHELL -l
fnm install 20 && fnm default 20
```

---

## Common recipes

### Bootstrap a new TypeScript project

```bash
mkdir my-app && cd my-app
pnpm init
pnpm add -D typescript @types/node tsx
npx tsc --init --rootDir src --outDir dist --strict --target es2022 --module nodenext
mkdir src && echo 'console.log("hi")' > src/index.ts
pnpm exec tsx src/index.ts
```

### Bootstrap a Vite + React + TS app

```bash
pnpm create vite@latest my-app -- --template react-ts
cd my-app && pnpm install && pnpm dev --host 0.0.0.0
```

### Bootstrap a Next.js app

```bash
pnpm create next-app@latest my-app --ts --tailwind --eslint --app --src-dir
cd my-app && pnpm dev
```

### Run lint + types + tests

```bash
pnpm lint                 # eslint or biome
pnpm typecheck            # often `tsc --noEmit`
pnpm test
# or unified
pnpm exec tsc --noEmit && pnpm exec eslint . && pnpm test
```

### Build for production

```bash
pnpm build                # vite/next/etc
pnpm preview              # vite preview
```

### Deploy a node service with pm2

```bash
pnpm add -g pm2           # only when needed
pm2 start "pnpm start" --name my-app --update-env
pm2 save && pm2 startup    # systemd unit
pm2 logs my-app
```

### Express minimum

```javascript
// src/server.js
import express from "express";
const app = express();
app.get("/", (_req, res) => res.json({ ok: true }));
app.listen(8080, () => console.log("listening on :8080"));
```

### Fastify (faster than express)

```javascript
import Fastify from "fastify";
const f = Fastify();
f.get("/", async () => ({ ok: true }));
await f.listen({ port: 8080, host: "0.0.0.0" });
```

---

## Debugging

- `MODULE_NOT_FOUND`: check `node_modules/` exists; rerun `pnpm install`.
- ESM vs CJS: package.json `"type": "module"` → import syntax. Without
  it, default is CJS (require). Keep imports consistent.
- Path aliases (`@/foo`): need both `tsconfig.json#paths` AND a runtime
  resolver (vite plugin, ts-node-paths, or compiled output handles it).
- Type errors blocking build: `tsc --noEmit` reproduces locally; fix
  one file at a time.
- Vite "fail to resolve": clear cache `rm -rf node_modules/.vite`.

---

## Pitfalls

- `npm install` on a `pnpm-lock.yaml` project corrupts the lockfile.
- Mixing `import` and `require` in the same file — use one only.
- Hard-coding `localhost` for `--host` in container/VPS — use `0.0.0.0`.
- Editing the wrong `tsconfig.json` in a monorepo (root vs package).
