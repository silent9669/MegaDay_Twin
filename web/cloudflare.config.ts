import { bindings, defineConfig, exports } from "cf/config";

export default defineConfig({
  accountId: "1177b93da7312e51a226b6ecffb1e251",
  worker: {
    name: "megadaytwin",
    entrypoint: "./worker.ts",
    compatibilityDate: "2026-10-30",
    compatibilityFlags: ["nodejs_compat"],
    domains: ["megadaytwin.dangphuc.me"],
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
        worker: "megadaytwin",
        exportName: "AccountWorkspace",
      }),
      ADMIN_PASSWORD_HASH: bindings.secret(),
      JUDGES_PASSWORD_HASH: bindings.secret(),
      SESSION_SECRET: bindings.secret(),
      OPENAI_API_KEY: bindings.secret(),
      OPENAI_MODEL_IMPORT: bindings.text("gpt-6-luna"),
      OPENAI_MODEL_DECISION: bindings.text("gpt-6-luna"),
      LOGIN_LIMITER: bindings.rateLimit({
        namespace: "2001",
        simple: { limit: 10, period: 60 },
      }),
      PM_LIMITER: bindings.rateLimit({
        namespace: "2002",
        simple: { limit: 20, period: 60 },
      }),
      DB: bindings.d1({
        name: "megadaytwin-db",
        id: "983d0ef9-dcc6-45d7-bf05-55f908e1189e",
      }),
    },
  },
});
