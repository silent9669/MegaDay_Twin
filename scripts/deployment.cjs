// Shared by GitHub Actions and dependency-free local regression tests.
async function needsDeployment(github, context) {
  if (context.eventName === 'workflow_dispatch') return true;
  const { data } = await github.rest.repos.compareCommitsWithBasehead({
    ...context.repo, basehead: `cloudflare-production...${context.sha}`,
  });
  // GitHub caps the comparison at 300 files; never skip an incomplete diff.
  if (!data.files || data.files.length >= 300) return true;
  const relevant = (path = '') => path.startsWith('web/') || path.startsWith('mock-data/out/') ||
    path === '.github/workflows/deploy.yml' || path === 'scripts/deployment.cjs' ||
    path === 'scripts/deployment.test.cjs';
  return data.files.some((file) => relevant(file.filename) || relevant(file.previous_filename));
}

async function promote(github, context) {
  if (context.ref !== 'refs/heads/main') throw new Error('Only main can deploy.');
  const { data: main } = await github.rest.repos.getBranch({ ...context.repo, branch: 'main' });
  if (main.commit.sha !== context.sha) {
    throw new Error('A newer main commit exists; refusing to deploy this older commit.');
  }
  await github.rest.git.updateRef({
    ...context.repo, ref: 'heads/cloudflare-production', sha: context.sha, force: false,
  });
}

async function waitForBuild(github, context, {
  now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  const deadline = now() + 10 * 60 * 1000;
  while (now() < deadline) {
    const checks = await github.paginate(github.rest.checks.listForRef, {
      ...context.repo, ref: context.sha, check_name: 'Workers Builds: megadaytwin', per_page: 100,
    });
    const check = checks
      .filter((run) => run.app?.slug === 'cloudflare-workers-and-pages' && run.head_sha === context.sha)
      .sort((a, b) => b.id - a.id)[0];
    if (check?.status === 'completed') {
      if (check.conclusion !== 'success') {
        throw new Error(`Cloudflare deployment ${check.conclusion}: ${check.details_url}`);
      }
      return check.details_url;
    }
    await sleep(10000);
  }
  throw new Error('Timed out waiting for Cloudflare deployment; production success is unconfirmed.');
}

async function verifyProduction(fetchImpl = fetch) {
  const base = 'https://megadaytwin.dangphuc.me';
  const request = (path) => fetchImpl(`${base}${path}`, {
    redirect: 'manual', signal: AbortSignal.timeout(15000),
  });
  const health = await request('/healthz');
  const body = await health.json();
  if (health.status !== 200 || body.status !== 'ok' || body.db !== 'ok') {
    throw new Error('Production D1 health check failed.');
  }
  const [login, privateChat, city] = await Promise.all([
    request('/login'), request('/api/workspace/chats'), request('/city-demo'),
  ]);
  const loginRedirect = new URL(city.headers.get('location') || '/', base);
  if (login.status !== 200 || privateChat.status !== 401 || city.status !== 307 ||
      loginRedirect.origin !== base || loginRedirect.pathname !== '/login') {
    throw new Error('Production login/access gate check failed.');
  }
}

module.exports = { needsDeployment, promote, waitForBuild, verifyProduction };
