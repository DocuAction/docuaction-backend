#!/usr/bin/env node
/**
 * Run the DocuAction API collection with Newman against an ISOLATED backend,
 * injecting synthetic credentials and the seeded ids from the fixtures
 * manifest so nothing sensitive is committed and no id is guessed.
 *
 * Self-contained: run from this directory after `npm ci` here, e.g.
 *   node run.mjs --manifest ../../integration-fixtures.json \
 *     --baseUrl http://127.0.0.1:8010 --junit newman-results.xml
 *
 * Refuses a non-local baseUrl — this harness only runs against a disposable
 * instance (the same guard the frontend live-smoke spec enforces).
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import newman from 'newman';

const here = path.dirname(fileURLToPath(import.meta.url));

function arg(name, dflt) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}

const collection = arg('collection', path.join(here, 'docuaction-api.postman_collection.json'));
const environment = arg('environment', path.join(here, 'docuaction-itest.postman_environment.json'));
const manifestPath = arg('manifest', path.join(here, '..', '..', 'integration-fixtures.json'));
const baseUrl = arg('baseUrl', 'http://127.0.0.1:8010');
const reporters = arg('reporters', 'cli,junit').split(',');
const junit = arg('junit', path.join(here, 'newman-results.xml'));

const host = new URL(baseUrl).hostname;
if (!['127.0.0.1', 'localhost', '::1'].includes(host)) {
  console.error(`refusing non-local backend ${host}: this harness only runs against an isolated instance`);
  process.exit(2);
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const byRole = Object.fromEntries((manifest.accounts || []).map((a) => [a.role, a]));

const envVars = [
  { key: 'baseUrl', value: baseUrl },
  { key: 'intake_id', value: manifest.intake_id || '' },
  { key: 'job_id', value: manifest.job_id || '' },
  { key: 'candidate_entity_id', value: manifest.candidate_entity_id || '' },
  { key: 'viewer_password', value: (byRole.viewer || {}).password || '' },
  { key: 'reviewer_password', value: (byRole.reviewer || {}).password || '' },
  { key: 'qalead_password', value: (byRole.qalead || {}).password || '' },
  { key: 'pm_password', value: (byRole.program_manager || {}).password || '' },
];

newman.run(
  {
    collection,
    environment,
    envVar: envVars,
    reporters,
    reporter: { junit: { export: junit } },
    bail: false,
  },
  (err, summary) => {
    if (err) {
      console.error('newman run error:', err);
      process.exit(1);
    }
    const s = summary.run.stats;
    console.log(`\nRequests ${s.requests.total}, assertions ${s.assertions.total}, failed ${s.assertions.failed}`);
    const failures = summary.run.failures || [];
    if (failures.length) {
      console.error(`\n${failures.length} assertion failure(s):`);
      for (const f of failures) {
        console.error(` - ${f.source && f.source.name}: ${f.error && f.error.message}`);
      }
      process.exit(1);
    }
    console.log('All Newman assertions passed.');
  }
);
