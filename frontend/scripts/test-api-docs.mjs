import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { JSDOM, VirtualConsole } from 'jsdom';

// Use the real FastAPI schema and shipped Swagger renderer, not an HTML status check.
const root = fileURLToPath(new URL('../../', import.meta.url));
const python = fileURLToPath(new URL(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python', import.meta.url));
const source = `import json, sys
from pathlib import Path
from metadata_service.api import create_app
from metadata_service.config import Settings
app = create_app(Settings(data_dir=Path(sys.argv[1])).initialize())
print(json.dumps(app.openapi()))`;
const directory = fileURLToPath(new URL(`../../data/swagger-render-${randomUUID()}/`, import.meta.url));
const spec = JSON.parse(execFileSync(python, ['-c', source, directory], { cwd: root, encoding: 'utf8', maxBuffer: 5 * 1024 * 1024 }));
assert.equal(spec.openapi, '3.1.0');
const assetRoot = new URL('../../metadata_service/static/swagger-ui/5.33.1/', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json', assetRoot), 'utf8'));
const bundle = await readFile(process.argv[2] ?? new URL('swagger-ui-bundle.js', assetRoot), 'utf8');
const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM('<!doctype html><div id="swagger-ui"></div>', { url: 'http://127.0.0.1/api/v1/docs', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole });
try {
  // jsdom omits Web APIs that browsers provide. No network requests are permitted.
  Object.assign(dom.window, { TextEncoder, TextDecoder, Request, Response, Headers });
  dom.window.scrollTo = () => {}; // jsdom has no visual scrolling implementation.
  dom.window.fetch = () => { throw new Error('Rendering test must not access the network'); };
  dom.window.eval(bundle);
  const ui = dom.window.SwaggerUIBundle({ spec, dom_id: '#swagger-ui', presets: [dom.window.SwaggerUIBundle.presets.apis], layout: 'BaseLayout', validatorUrl: null });
  const count = Object.values(spec.paths).reduce((total, methods) => total + Object.keys(methods).length, 0);
  const deadline = Date.now() + 10000;
  while (dom.window.document.querySelectorAll('.opblock').length < count && Date.now() < deadline) {
    if (dom.window.document.body.textContent.includes('Unable to render this definition')) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert(!dom.window.document.body.textContent.includes('Unable to render this definition'), 'Swagger rejected the actual OpenAPI 3.1 schema');
  assert.equal(dom.window.document.querySelectorAll('.opblock').length, count, 'All API operations must render');
  assert.equal(ui.getSystem().specSelectors.isOAS31(), true);
  assert(dom.window.document.body.textContent.includes('创建异步分析任务'));
  assert.deepEqual(errors, []);
  console.log(`API_DOCS_RENDER_OK swagger=${manifest.version} openapi=${spec.openapi} operations=${count} network=false`);
} finally { dom.window.close(); }
