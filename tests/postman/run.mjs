#!/usr/bin/env node
/**
 * Run the DocuAction API collection with the **Postman CLI** against an
 * ISOLATED backend. Dependency-free (Node built-ins only) so there is no npm
 * dependency tree to maintain or scan — it shells out to the official
 * `postman` binary, which runs a LOCAL collection file with no API key
 * (results are simply not synced to Postman cloud; see
 * https://learning.postman.com/docs/postman-cli/postman-cli-run-collection/).
 *
 * Injects the synthetic credentials and seeded ids from the fixtures manifest
 * as --env-var overrides, so nothing sensitive is committed and no id is
 * guessed. Refuses a non-local baseUrl.
 *
 * Usage (after installing the Postman CLI — see tests/postman/README.md):
 *   node run.mjs --manifest ../../integration-fixtures.json \
 *     --baseUrl http://127.0.0.1:8010 --junit newman-results.xml
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const arg = (name, dflt) => {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
};

const collection = arg('collection', path.join(here, 'docuaction-api.postman_collection.json'));
const environment = arg('environment', path.join(here, 'docuaction-itest.postman_environment.json'));
const manifestPath = arg('manifest', path.join(here, '..', '..', 'integration-fixtures.json'));
const baseUrl = arg('baseUrl', 'http://127.0.0.1:8010');
const junit = arg('junit', path.join(here, 'newman-results.xml'));

const host = new URL(baseUrl).hostname;
if (!['127.0.0.1', 'localhost', '::1'].includes(host)) {
  console.error(`refusing non-local backend ${host}: this harness only runs against an isolated instance`);
  process.exit(2);
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const byRole = Object.fromEntries((manifest.accounts || []).map((a) => [a.role, a]));
const envVars = {
  baseUrl,
  intake_id: manifest.intake_id || '',
  job_id: manifest.job_id || '',
  candidate_entity_id: manifest.candidate_entity_id || '',
  viewer_password: (byRole.viewer || {}).password || '',
  reviewer_password: (byRole.reviewer || {}).password || '',
  qalead_password: (byRole.qalead || {}).password || '',
  pm_password: (byRole.program_manager || {}).password || '',
};

const args = ['collection', 'run', collection, '-e', environment,
  '-r', 'cli,junit', '--reporter-junit-export', junit];
for (const [k, v] of Object.entries(envVars)) args.push('--env-var', `${k}=${v}`);

const bin = process.env.POSTMAN_CLI || 'postman';
const res = spawnSync(bin, args, { stdio: 'inherit' });
if (res.error && res.error.code === 'ENOENT') {
  console.error(`\nPostman CLI ('${bin}') not found on PATH. Install it:\n` +
    `  Linux/macOS: curl -o- "https://dl-cli.pstmn.io/install/linux64.sh" | sh\n` +
    `  Windows:     powershell -c "iwr https://dl-cli.pstmn.io/install/win64/latest -UseBasicParsing | iex"\n` +
    `  (see tests/postman/README.md). A local run needs no Postman API key.`);
  process.exit(127);
}
process.exit(res.status == null ? 1 : res.status);
