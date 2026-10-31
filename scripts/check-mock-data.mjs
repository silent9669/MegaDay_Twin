// Generate outside the checkout so verification never overwrites local data.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const temporary = mkdtempSync(join(tmpdir(), 'megaday-data-check-'));
function files(directory) {
  return readdirSync(directory, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => join(entry.parentPath, entry.name).slice(directory.length + 1))
    .sort();
}
try {
  for (const size of ['nho', 'demo']) {
    const generated = join(temporary, size);
    const result = spawnSync(process.execPath, ['mock-data/generate.js', '--size', size, '--out', generated], {
      cwd: root, encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.error?.message || result.stderr || 'Mock-data generation failed');
    const committed = join(root, 'mock-data/out', size);
    assert.deepEqual(files(generated), files(committed), `${size}: generated file list differs`);
    for (const file of files(generated)) {
      // Match the previous CI policy: manifests contain generation timestamps.
      if (file === join('ground_truth', 'manifest.json')) continue;
      assert.ok(readFileSync(join(generated, file)).equals(readFileSync(join(committed, file))),
        `${size}/${file}: generated data differs`);
    }
    console.log(`${size}: generated data matches committed fixtures`);
  }
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
