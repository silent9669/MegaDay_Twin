import { config, type Role } from '../lib/config';
import { viText } from '../lib/i18n';
import type { ApiErrorBody } from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | undefined;

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/**
 * Maps an API path (without query string) to a fixture file in ./fixtures. The files are a
 * snapshot of the real API at 01:00 on 11.11 (backend/eval/record_fixtures.py), except the
 * illustrative Compare case rec-510/scn-2210
 * (page-10 example), which is hand-written (M3-web.md).
 */
const FIXTURE_ROUTES: Record<string, string> = {
  '/health': 'health',
  '/me': 'me',
  '/freshness': 'freshness',
  '/live/status': 'live-status',
  '/skus': 'skus',
  '/recommendations': 'recommendations',
  '/alerts': 'alerts',
};

/** /recommendations/{id} → recommendation-{id}.json, and the same for scenarios, campaigns, skus. */
const FIXTURE_BY_ID: [RegExp, string][] = [
  [/^\/recommendations\/([^/]+)$/, 'recommendation'],
  [/^\/scenarios\/([^/]+)$/, 'scenario'],
  [/^\/campaigns\/([^/]+)$/, 'campaign'],
  [/^\/skus\/([^/]+)$/, 'sku'],
];

const fixtureLoaders = import.meta.glob<unknown>('./fixtures/*.json', { import: 'default' });

function fixtureName(bare: string): string | undefined {
  const fixed = FIXTURE_ROUTES[bare];
  if (fixed) return fixed;
  for (const [re, kind] of FIXTURE_BY_ID) {
    const m = re.exec(bare);
    if (m) return `${kind}-${decodeURIComponent(m[1] ?? '')}`;
  }
  return undefined;
}

async function loadFixture(path: string): Promise<unknown> {
  const bare = path.split('?')[0] ?? path;
  const name = fixtureName(bare);
  const loader = name ? fixtureLoaders[`./fixtures/${name}.json`] : undefined;
  if (loader) {
    // structuredClone keeps callers from mutating the shared fixture module.
    return structuredClone(await loader());
  }
  // GET /skus/{sku} without its own file: answer from the list fixture (the detail adds
  // fields the UI reads as optional).
  const skuMatch = /^\/skus\/([^/]+)$/.exec(bare);
  const listLoader = fixtureLoaders['./fixtures/skus.json'];
  if (skuMatch && listLoader) {
    const list = (await listLoader()) as { data: { sku: string }[]; meta?: unknown };
    const row = list.data.find((r) => r.sku === decodeURIComponent(skuMatch[1] ?? ''));
    if (row) return structuredClone({ data: row, meta: list.meta });
  }
  throw new ApiError(404, 'NOT_FOUND', viText.errors.fixtureMissing(path));
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  return typeof value === 'object' && value !== null && 'error' in value;
}

export type ApiClientOptions = {
  baseUrl: string;
  useFixtures: boolean;
  user: { id: string; role: Role };
  fetchImpl?: typeof fetch;
};

export type RequestOptions = {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  idempotencyKey?: string;
};

export function createApiClient(opts: ApiClientOptions) {
  const doFetch = opts.fetchImpl ?? ((...args: Parameters<typeof fetch>) => fetch(...args));

  async function request<T>(path: string, req: RequestOptions = {}): Promise<T> {
    if (opts.useFixtures) {
      return (await loadFixture(path)) as T;
    }

    const headers: Record<string, string> = {
      Accept: 'application/json',
      'X-User': opts.user.id,
      'X-Role': opts.user.role,
    };
    if (req.body !== undefined) headers['Content-Type'] = 'application/json';
    if (req.idempotencyKey) headers['Idempotency-Key'] = req.idempotencyKey;

    let res: Response;
    try {
      res = await doFetch(`${opts.baseUrl}${path}`, {
        method: req.method ?? 'GET',
        headers,
        body: req.body === undefined ? undefined : JSON.stringify(req.body),
        signal: req.signal,
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') throw err;
      throw new ApiError(0, 'NETWORK_ERROR', viText.errors.network);
    }

    const text = await res.text();
    let json: unknown = undefined;
    if (text) {
      try {
        json = JSON.parse(text);
      } catch {
        json = undefined;
      }
    }

    if (!res.ok) {
      if (isErrorBody(json)) {
        const { code, message, details } = json.error;
        throw new ApiError(res.status, code, message, details);
      }
      throw new ApiError(res.status, 'HTTP_ERROR', viText.errors.http(res.status));
    }
    return json as T;
  }

  return {
    get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
    post: <T>(path: string, body?: unknown, extra?: Omit<RequestOptions, 'method' | 'body'>) =>
      request<T>(path, { ...extra, method: 'POST', body }),
    put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
    patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

export const api = createApiClient({
  baseUrl: config.apiBaseUrl,
  useFixtures: config.useFixtures,
  user: config.user,
});
