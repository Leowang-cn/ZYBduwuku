import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';

const root = path.resolve(import.meta.dirname, '..');
const temporary = await fs.mkdtemp(path.join(root, '.paste-tests-'));
try {
  const source = await fs.readFile(path.join(root, 'web', 'paste.ts'), 'utf8');
  await fs.writeFile(path.join(temporary, 'paste.mjs'), ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText);
  const {planImagePaste} = await import(pathToFileURL(path.join(temporary, 'paste.mjs')));
  const png = (name = 'image.png', size = 10) => new File([new Uint8Array(size)], name, {type: 'image/png'});
  const plan = clip => planImagePaste({types: [], html: '', plain: '', files: [], ...clip});

  test('pastes text normally when the clipboard has no files', () => {
    assert.deepEqual(plan({types: ['text/plain'], plain: 'hello'}), {pasteText: 'default', upload: [], rejected: []});
  });

  test('uploads a screenshot without pasting text', () => {
    const file = png();
    const result = plan({types: ['Files'], files: [file]});
    assert.equal(result.pasteText, 'none');
    assert.deepEqual(result.upload, [file]);
  });

  test('ignores file-name text that Finder adds when copying files', () => {
    const file = png('shot.png');
    const result = plan({types: ['text/plain', 'Files'], plain: 'shot.png', files: [file]});
    assert.equal(result.pasteText, 'none');
    assert.deepEqual(result.upload, [file]);
  });

  test('keeps text and uploads images when HTML has no displayable image', () => {
    const file = png();
    const result = plan({types: ['text/html', 'text/plain', 'Files'], html: '<p>步骤一</p>', plain: '步骤一', files: [file]});
    assert.equal(result.pasteText, 'default');
    assert.deepEqual(result.upload, [file]);
  });

  test('does not upload a duplicate of a remote image already in HTML', () => {
    const result = plan({types: ['text/html', 'Files'], html: '<p>说明</p><img src="https://example.com/a.png">', files: [png()]});
    assert.deepEqual(result, {pasteText: 'default', upload: [], rejected: []});
  });

  test('replaces images with unsaveable sources by uploaded files', () => {
    const file = png();
    const result = plan({types: ['text/html', 'Files'], html: '<p>说明</p><img src="file:///tmp/a.png"><img src=\'data:image/png;base64,AA\'>', files: [file]});
    assert.equal(result.pasteText, 'html');
    assert.equal(result.html, '<p>说明</p>');
    assert.deepEqual(result.upload, [file]);
  });

  test('skips the whole-selection rendering that Word and WPS attach', () => {
    const html = '<html xmlns:o="urn:schemas-microsoft-com:office:office"><body><p>正文</p><img src="file:///C:/tmp/clip_image001.png"></body></html>';
    const result = plan({types: ['text/html', 'text/plain', 'Files'], html, plain: '正文', files: [png()]});
    assert.equal(result.pasteText, 'html');
    assert.ok(!result.html.includes('<img'));
    assert.deepEqual(result.upload, []);
    assert.match(result.notice, /单独复制图片/);
  });

  test('uploads a single image copied alone from Word', () => {
    const file = png();
    const html = '<html xmlns:o="urn:schemas-microsoft-com:office:office"><body><img src="file:///C:/tmp/clip_image001.png"></body></html>';
    const result = plan({types: ['text/html', 'Files'], html, files: [file]});
    assert.equal(result.pasteText, 'none');
    assert.deepEqual(result.upload, [file]);
  });

  test('rejects unsupported or oversized images', () => {
    const svg = new File(['<svg/>'], 'a.svg', {type: 'image/svg+xml'});
    const big = png('big.png', 5 * 1024 * 1024 + 1);
    const ok = png('ok.png');
    const result = plan({types: ['Files'], files: [svg, big, ok]});
    assert.deepEqual(result.upload, [ok]);
    assert.deepEqual(result.rejected, [svg, big]);
  });

  test('ignores non-image files and reports it', () => {
    const pdf = new File(['x'], 'a.pdf', {type: 'application/pdf'});
    const result = plan({types: ['Files'], files: [pdf]});
    assert.deepEqual(result.upload, []);
    assert.match(result.notice, /仅支持粘贴图片/);
  });

  test('leaves code blocks and BlockNote internal copies to the default handler', () => {
    assert.deepEqual(plan({types: ['text/plain', 'Files'], plain: 'code', files: [png()], inCodeBlock: true}).upload, []);
    assert.deepEqual(plan({types: ['blocknote/html', 'text/html', 'Files'], html: '<p>x</p>', files: [png()]}).upload, []);
  });
} finally {
  test.after(() => fs.rm(temporary, {recursive: true, force: true}));
}
