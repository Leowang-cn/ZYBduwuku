import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MantineProvider} from '@mantine/core';

const root = path.resolve(import.meta.dirname, '..');
const temporary = await fs.mkdtemp(path.join(root, '.release-tests-'));
try {
  for (const name of ['releases', 'release-editor']) {
    const source = await fs.readFile(path.join(root, 'web', `${name}.tsx`), 'utf8');
    await fs.writeFile(path.join(temporary, `${name}.mjs`), ts.transpileModule(source, {compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022}}).outputText);
  }
  const {groupVersions, ReleaseDownloads} = await import(pathToFileURL(path.join(temporary, 'releases.mjs')));
  const {ReleaseEditor} = await import(pathToFileURL(path.join(temporary, 'release-editor.mjs')));
  const render = (Component, props) => renderToStaticMarkup(React.createElement(MantineProvider, {}, React.createElement(Component, props)));
  const releases = [
    {version: '11.1.1', platform: 'Windows', size: '10 MB', created_at: '2026-12-20', deliverables: [{id: 1, kind: 'file', filename: 'long-file.exe'}]},
    {version: '11.1.1', platform: 'macOS', size: '11 MB', created_at: '2026-12-19', deliverables: [{id: 2, kind: 'file', filename: 'long-file.dmg'}]},
    {version: '11.0.0', deliverables: [{id: 3, kind: 'url', platform: 'iOS', url: 'https://example.com/download', updated_at: '2026-12-18'}]},
  ];
  test('groups equal versions across platforms without mutating existing data', () => {
    const before = JSON.stringify(releases);
    const groups = groupVersions(releases);
    assert.equal(groups.length, 2);
    assert.deepEqual(groups[0].deliverables.map(item => item.platform), ['Windows', 'macOS']);
    assert.equal(JSON.stringify(releases), before);
  });
  test('renders compact per-platform rows and collapsed version history', () => {
    const html = render(ReleaseDownloads, {name: '百度网盘', releases, isProduct: true});
    assert.equal((html.match(/class="download-row"/g) || []).length, 3);
    assert.ok(html.includes('/api/download/1') && html.includes('/api/download/2'));
    assert.ok(html.includes('10 MB') && html.includes('11 MB'));
    assert.ok(html.includes('<details') && !html.includes('<details open'));
    assert.ok(!html.includes('long-file.exe') && !html.includes('未提供安装包大小'));
  });
  test('subapps keep independent version sections and suppress empty parent downloads', () => {
    assert.equal(render(ReleaseDownloads, {name: 'minicale', releases: [], isProduct: true, hasSubapps: true}).includes('最新版本与下载'), false);
    assert.ok(render(ReleaseDownloads, {name: 'minicale PC端', releases}).includes('minicale PC端'));
  });
  test('explicit package metadata overrides legacy release metadata', () => {
    const [group] = groupVersions([{version: '1', platform: 'Windows', size: 'old', deliverables: [{platform: 'macOS', size: '', updated_at: '2026-01-02'}]}]);
    assert.equal(group.deliverables[0].platform, 'macOS');
    assert.equal(group.deliverables[0].size, '');
  });
  test('admin editor provides metadata per download and visible upload progress', () => {
    const html = render(ReleaseEditor, {release: releases[0], platforms: ['Windows', 'macOS'], onUpdate() {}, upload() {}, uploadState: {status: 'uploading', percent: 42}});
    for (const label of ['端类型', '包大小（可选）', '更新时间', '正在上传：42%', '添加下载地址', '为此版本添加不同端的安装包', '安装包端类型', '上传该端安装包']) assert.ok(html.includes(label));
  });
} finally {
  await fs.rm(temporary, {recursive: true, force: true});
}
