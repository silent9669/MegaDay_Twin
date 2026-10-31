/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_USE_FIXTURES?: string;
  readonly VITE_DEV_USER?: string;
  readonly VITE_DEV_USER_NAME?: string;
  readonly VITE_DEV_ROLE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
