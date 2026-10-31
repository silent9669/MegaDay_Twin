const { test } = require('node:test');
const assert = require('node:assert/strict');
const { deploymentChanges, promote, waitForBuild, verifyProduction } = require('./deployment.cjs');

const context = { repo: { owner: 'example', repo: 'demo' }, sha: 'current', ref: 'refs/heads/main' };
const build = (overrides = {}) => ({
  id: 1, head_sha: 'current', status: 'completed', conclusion: 'success',
  app: { slug: 'cloudflare-workers-and-pages' }, details_url: 'https://example.test/build',
  ...overrides,
});

test('a docs merge still deploys web changes pending since the production commit', async () => {
  const github = { rest: { repos: { compareCommitsWithBasehead: async (options) => {
    assert.equal(options.basehead, 'cloudflare-production...current');
    return { data: { files: [{ filename: 'web/src/App.tsx' }, { filename: 'README.md' }] } };
  } } } };
  assert.equal((await deploymentChanges(github, context)).deploy, true);
});

for (const [name, files, expected] of [
  ['docs only', [{ filename: 'README.md' }], false],
  ['Python backend', [{ filename: 'backend/app/main.py' }], true],
  ['backend workflow', [{ filename: '.github/workflows/deploy-backend.yml' }], true],
  ['already promoted', [], false],
  ['bundled data', [{ filename: 'mock-data/out/demo/orders.csv' }], true],
  ['deployment workflow', [{ filename: '.github/workflows/deploy.yml' }], true],
  ['deployment script', [{ filename: 'scripts/deployment.cjs' }], true],
  ['file moved out of web', [{ filename: 'archive/old.ts', previous_filename: 'web/old.ts' }], true],
  ['truncated comparison', Array.from({ length: 300 }, (_, n) => ({ filename: `docs/${n}.md` })), true],
  ['missing comparison', undefined, true],
]) {
  test(`deployment selection: ${name}`, async () => {
    const github = { rest: { repos: {
      compareCommitsWithBasehead: async () => ({ data: { files } }),
    } } };
    assert.equal((await deploymentChanges(github, context)).deploy, expected);
  });
}

test('manual dispatch verifies production even when no changes are pending', async () => {
  assert.equal((await deploymentChanges({}, { ...context, eventName: 'workflow_dispatch' })).deploy, true);
});

test('promotes the exact main commit without force', async () => {
  let updated;
  const promoted = await promote({ rest: {
    repos: { getBranch: async () => ({ data: { commit: { sha: 'current' } } }) },
    git: { updateRef: async (value) => { updated = value; } },
  } }, context);
  assert.equal(promoted, true);
  assert.deepEqual(updated, { ...context.repo, ref: 'heads/cloudflare-production', sha: 'current', force: false });
});

test('skips superseded commits and rejects non-main refs without promoting', async () => {
  let promoted = false;
  const github = { rest: {
    repos: { getBranch: async () => ({ data: { commit: { sha: 'newer' } } }) },
    git: { updateRef: async () => { promoted = true; } },
  } };
  assert.equal(await promote(github, context), false);
  await assert.rejects(promote(github, { ...context, ref: 'refs/heads/feature' }), /main/);
  assert.equal(promoted, false);
});

test('real promotion errors still fail instead of being treated as superseded', async () => {
  const github = { rest: {
    repos: { getBranch: async () => ({ data: { commit: { sha: 'current' } } }) },
    git: { updateRef: async () => { throw new Error('permission denied'); } },
  } };
  await assert.rejects(promote(github, context), /permission denied/);
  github.rest.repos.getBranch = async () => { throw new Error('network unavailable'); };
  await assert.rejects(promote(github, context), /network unavailable/);
});

test('workflow stops after a superseded promotion without claiming production health', async () => {
  const { readFileSync } = require('node:fs');
  const workflow = readFileSync(`${__dirname}/../.github/workflows/deploy.yml`, 'utf8');
  const script = workflow.split('script: |').at(-1).split('\n')
    .map(line => line.replace(/^            /, '')).join('\n');
  const messages = [];
  const core = {
    notice: message => messages.push(message),
    summary: { addRaw(message) { messages.push(message); return this; }, async write() {} },
  };
  const unexpected = async () => assert.fail('Superseded runs must not wait or verify');
  const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
  await new AsyncFunction('require', 'github', 'context', 'core', script)(
    () => ({ promote: async () => false, waitForBuild: unexpected, verifyProduction: unexpected }),
    {}, context, core,
  );
  assert.ok(messages.some(message => message.includes('newer main')));
  assert.ok(!messages.some(message => message.includes('passed')));
});

function poll(batches) {
  let time = 0;
  let calls = 0;
  const github = {
    rest: { checks: { listForRef() {} } },
    paginate: async (_, options) => {
      assert.equal(options.ref, context.sha);
      assert.equal(options.check_name, 'Workers Builds: megadaytwin');
      return batches[Math.min(calls++, batches.length - 1)];
    },
  };
  return {
    github, calls: () => calls,
    clock: { now: () => time, sleep: async (ms) => { time += ms; } },
  };
}

test('waits for the latest matching Cloudflare build, ignoring other apps and commits', async () => {
  const p = poll([
    [build({ id: 2, status: 'in_progress', conclusion: null }), build()],
    [build({ id: 2 }), build({ id: 3, head_sha: 'older', conclusion: 'failure' }),
      build({ id: 4, app: { slug: 'other' }, conclusion: 'failure' })],
  ]);
  assert.equal(await waitForBuild(p.github, context, p.clock), 'https://example.test/build');
  assert.equal(p.calls(), 2);
});

for (const conclusion of ['failure', 'cancelled', 'timed_out', 'skipped', 'neutral']) {
  test(`rejects a ${conclusion} Cloudflare build`, async () => {
    const p = poll([[build({ conclusion })]]);
    await assert.rejects(waitForBuild(p.github, context, p.clock), /Cloudflare deployment/);
  });
}

test('times out when the matching build never completes', async () => {
  const p = poll([[build({ status: 'in_progress', conclusion: null })]]);
  await assert.rejects(waitForBuild(p.github, context, p.clock), /Timed out/);
  assert.equal(p.calls(), 60);
});

const responses = () => ({
  '/healthz': Response.json({ status: 'ok', db: 'ok' }),
  '/login': new Response('login'),
  '/api/workspace/chats': new Response(null, { status: 401 }),
  '/city-demo': new Response(null, { status: 307, headers: { location: '/login' } }),
});
const request = (routes) => async (url, options) => {
  assert.equal(options.redirect, 'manual');
  assert.ok(options.signal instanceof AbortSignal);
  return routes[new URL(url).pathname];
};

test('verifies live D1 health, login and authentication gates without following redirects', async () => {
  await verifyProduction(request(responses()));
});

for (const [path, response] of [
  ['/healthz', Response.json({ status: 'ok', db: 'error' })],
  ['/healthz', Response.json({ status: 'ok', db: 'ok' }, { status: 503 })],
  ['/login', new Response(null, { status: 500 })],
  ['/api/workspace/chats', Response.json([])],
  ['/city-demo', new Response('private city')],
  ['/city-demo', new Response(null, { status: 307, headers: { location: '/broken' } })],
]) {
  test(`rejects unhealthy production response for ${path} (${response.status})`, async () => {
    await assert.rejects(verifyProduction(request({ ...responses(), [path]: response })));
  });
}

test('fails on network errors instead of reporting deployment success', async () => {
  await assert.rejects(verifyProduction(async () => { throw new Error('network unavailable'); }), /network/);
});

for (const [filename, backend] of [
  ['web/src/App.tsx', false], ['web/worker.ts', false],
  ['backend/app/main.py', true], ['web/decision-backend/worker.ts', true],
  ['web/package-lock.json', true], ['mock-data/out/demo/orders.csv', true],
  ['.github/workflows/deploy-backend.yml', true], ['scripts/hosted-smoke.py', true],
  ['README.md', false],
]) {
  test(`backend rebuild selection: ${filename}`, async () => {
    const github = { rest: { repos: { compareCommitsWithBasehead: async () => ({ data: { files: [{ filename }] } }) } } };
    assert.equal((await deploymentChanges(github, context)).backend, backend);
  });
}
test('backend moves and incomplete diffs still rebuild', async () => {
  for (const files of [undefined, Array.from({length:300},()=>({filename:'README.md'})),
    [{filename:'archive/old.py',previous_filename:'backend/app/old.py'}]]) {
    const github = { rest: { repos: { compareCommitsWithBasehead: async () => ({ data: { files } }) } } };
    assert.equal((await deploymentChanges(github, context)).backend, true);
  }
});
