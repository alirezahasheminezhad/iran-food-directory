import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
for (const script of ['export-csv.mjs', 'export-xlsx.mjs']) {
  execFileSync(process.execPath, [path.join(ROOT, 'scripts', script)], { stdio: 'inherit' });
}