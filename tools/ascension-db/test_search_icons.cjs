const {test} = require('node:test');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const modulePromise = import(pathToFileURL(path.join(__dirname, 'web/search-controls.js')).href);
const row = icon => ['fid/0/0', 'Name', 'item', 'Unspecified', 'Client captures', '1', ...(icon === undefined ? [] : [icon])];

test('icons render only from the build-provided base', async () => {
  const {iconMarkup} = await modulePromise;
  const manifest = {icons: {base: 'https://hertigservices.github.io/ascension-assets/icons/', extension: '.png'}};
  const html = iconMarkup(row('inv_misc_questionmark'), manifest);
  assert.ok(html.startsWith('<img class="result-icon"'));
  assert.ok(html.includes('src="https://hertigservices.github.io/ascension-assets/icons/inv_misc_questionmark.png"'));
  assert.ok(html.includes('loading="lazy"'));
  assert.equal(iconMarkup(row(), manifest), '<span class="result-icon is-empty" aria-hidden="true"></span>');
  assert.equal(iconMarkup(row('x'), {}), '', 'no icon column when the build published no icon base');
  assert.equal(iconMarkup(row('x'), {icons: {base: 'icons/'}}).includes('src="icons/x.png"'), true);
});

test('unsafe icon bases are refused', async () => {
  const {iconMarkup} = await modulePromise;
  for (const base of ['javascript:alert(1)//', 'http://example.com/', 'data:image/png;base64,', '//evil.example/'])
    assert.equal(iconMarkup(row('x'), {icons: {base}}), '', base);
});

test('icon names cannot break out of the attribute', async () => {
  const {iconMarkup} = await modulePromise;
  const html = iconMarkup(row('a" onerror="alert(1)'), {icons: {base: 'icons/'}});
  assert.ok(!html.includes('" onerror'));
  assert.ok(html.includes('icons/a%22%20onerror%3D%22alert(1).png'));
});
