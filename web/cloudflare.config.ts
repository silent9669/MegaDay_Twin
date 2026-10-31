import { bindings, defineConfig, exports } from "cf/config";

export default defineConfig({
  accountId: "1177b93da7312e51a226b6ecffb1e251",
  worker: {
    name: "megaday-hackathon",
    entrypoint: "./worker.ts",
    compatibilityDate: "2026-10-30",
    compatibilityFlags: ["nodejs_compat"],
    domains: ["hackathon.dangphuc.me"],
    workersDev: false,
    previewUrls: false,
    assets: {
      notFoundHandling: "single-page-application",
      runWorkerFirst: true,
    },
    exports: { AccountWorkspace: exports.durableObject({ storage: "sqlite" }) },
    env: {
      ASSETS: bindings.assets(),
      WORKSPACE: bindings.durableObject({
        worker: "megaday-hackathon",
        exportName: "AccountWorkspace",
      }),
      ADMIN_PASSWORD_HASH: bindings.secret(),
      SESSION_SECRET: bindings.secret(),
      OPENAI_API_KEY: bindings.secret(),
      LOGIN_LIMITER: bindings.rateLimit({
        namespace: "2001",
        simple: { limit: 10, period: 60 },
      }),
      PM_LIMITER: bindings.rateLimit({
        namespace: "2002",
        simple: { limit: 20, period: 60 },
      }),
      DB: bindings.d1({
        name: "hackathon-db",
        id: "37eca8be-2aba-4aa1-bdb9-9fbf9e1ec79f",
      }),
    },
  },
});
