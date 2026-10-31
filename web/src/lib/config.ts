export type Role = 'operator' | 'lead' | 'viewer';

const env = import.meta.env;

function readRole(value: string | undefined): Role {
  return value === 'lead' || value === 'viewer' ? value : 'operator';
}

export const config = {
  apiBaseUrl: (env.VITE_API_BASE_URL || '/api/v1').replace(/\/+$/, ''),
  useFixtures: env.VITE_USE_FIXTURES === 'true',
  user: {
    id: env.VITE_DEV_USER || 'u-ops-1',
    name: env.VITE_DEV_USER_NAME || 'Lan',
    role: readRole(env.VITE_DEV_ROLE),
  },
} as const;
