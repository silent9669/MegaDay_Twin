import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, waitFor } from '@testing-library/react';
import { expect, it } from 'vitest';
import { BackendRefreshBridge } from './BackendRefreshBridge';

it.each(['workspace:backend-refresh', 'workspace:campaign-update'])('invalidates live views on %s while keeping saved scenarios and receipts', async event => {
  const client = new QueryClient();
  const keys = [['sku', 'A'], ['skus', {}], ['campaign', 'C'], ['recommendations', {}], ['freshness'], ['scenario', 'saved'], ['decisions', 'receipt']];
  keys.forEach(key => client.setQueryData(key, { saved: true }));
  const view = render(<QueryClientProvider client={client}><BackendRefreshBridge /></QueryClientProvider>);
  act(() => { window.dispatchEvent(new CustomEvent(event, { detail: { reason: 'goal-applied' } })); });
  await waitFor(() => expect(client.getQueryState(['campaign', 'C'])?.isInvalidated).toBe(true));
  for (const key of keys.slice(0, 5)) expect(client.getQueryState(key)?.isInvalidated).toBe(true);
  for (const key of keys.slice(5)) expect(client.getQueryState(key)?.isInvalidated).toBe(false);
  view.unmount();
  client.setQueryData(['sku', 'A'], { new: true });
  window.dispatchEvent(new Event(event));
  expect(client.getQueryState(['sku', 'A'])?.isInvalidated).toBe(false);
  client.clear();
});
