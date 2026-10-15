/*
 * Mei-specific Light-only Archify post-processing.
 * Derived from FRAG-harness's MIT-licensed light architecture transform.
 */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname } from 'node:path';

const diagrams = ['memory-v2.workflow'];
const marker = 'data-frag-archify-light="true"';
const hash = (value) => createHash('sha256').update(value).digest('hex');
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const replaceOnce = (text, pattern, replacement, description) => {
  const matches = [...text.matchAll(new RegExp(pattern.source, pattern.flags.includes('g') ? pattern.flags : pattern.flags + 'g'))];
  assert(matches.length === 1, `Unsupported Archify output: ${description} (${matches.length} matches).`);
  return text.replace(pattern, replacement);
};
const removeDarkRules = (text) => text.replace(
  /(^|\n)[^\n{}]*\[data-theme="dark"\][^\n{}]*\{[^{}]*\}/g, '$1');
const htmlSvgBytes = (text) => [...text.matchAll(/<svg\b[\s\S]*?<\/svg>/g)].map((match) => match[0]).join('\n');
const svgSemanticBytes = (text) => text.replace(/<style\b[^>]*>[\s\S]*?<\/style>/g, '')
  .replace(/ data-frag-archify-light="true"| data-theme="light"/g, '');

function lightHtml(original) {
  if (original.includes(marker)) return original.includes("btn.style.display = 'none';") ? original
    : replaceOnce(original, /btn.disabled = true;\s*btn.hidden = true;\s*btn.setAttribute\('aria-disabled', 'true'\);/,
      "btn.disabled = true;\n      btn.hidden = true;\n      btn.style.display = 'none';\n      btn.setAttribute('aria-disabled', 'true');", 'theme button containment');
  assert(original.includes('content="archify 2.17.0-dev.1"'), 'Unsupported Archify generator version.');
  let text = replaceOnce(original, /<html lang="zh-CN" data-theme="dark" data-preset="classic">/,
    `<html lang="zh-CN" data-theme="light" data-preset="classic" ${marker}>`, 'HTML root');
  text = replaceOnce(text, /<script>\s*\/\/ Resolve the theme before first paint[\s\S]*?<\/script>/, `<script>
    (function () {
      var html = document.documentElement;
      html.setAttribute('data-theme', 'light');
      try {
        var params = new URLSearchParams(window.location.search);
        if (params.get('embed') === '1') html.setAttribute('data-embed', 'true');
        if (params.get('present') === '1') html.setAttribute('data-present', 'true');
      } catch (_) {}
    })();
  </script>`, 'first-paint theme resolution');
  text = replaceOnce(text, /Archify\.theme = \(function \(\) \{[\s\S]*?return \{ toggle: toggle \};\s*\}\)\(\);/, `Archify.theme = (function () {
      var html = document.documentElement;
      var btn = document.getElementById('btn-theme');
      var label = document.getElementById('theme-label');
      html.setAttribute('data-theme', 'light');
      label.textContent = viewerText('viewer.theme.light');
      btn.disabled = true;
      btn.hidden = true;
      btn.style.display = 'none';
      btn.setAttribute('aria-disabled', 'true');
      btn.setAttribute('aria-pressed', 'true');
      return { toggle: function () { html.setAttribute('data-theme', 'light'); } };
    })();`, 'runtime theme controller');
  text = replaceOnce(text, /<span id="theme-label">深色<\/span>/,
    '<span id="theme-label">浅色</span>', 'initial theme label');
  text = replaceOnce(text, / else if \(e\.key === 't' \|\| e\.key === 'T'\) \{\s*e\.preventDefault\(\);\s*Archify\.theme\.toggle\(\);\s*\}/,
    '', 'theme shortcut');
  text = replaceOnce(text, /serializeSvg\(1, \{ autoTheme: true \}\)/,
    'serializeSvg(1, { autoTheme: false })', 'canonical SVG export');
  text = removeDarkRules(text);
  assert(htmlSvgBytes(original) === htmlSvgBytes(text), 'HTML transformation changed authored SVG bytes.');
  return text;
}

function lightSvg(original) {
  if (original.includes(marker)) return original;
  let text = replaceOnce(original, /<svg\b([^>]*)>/,
    `<svg$1 data-theme="light" ${marker}>`, 'SVG root');
  if (text.includes('prefers-color-scheme')) {
    text = replaceOnce(text,
      /\n:root, svg \{ [^{}]* \}\n@media \(prefers-color-scheme: light\) \{ :root, svg \{ ([^{}]*) \} \}\n/,
      '\n:root, svg { $1 }\n', 'dual-theme SVG variables');
  } else {
    // Canonical export from the light-only viewer already contains the selected light variables.
    assert(/--bg: #f8fafc;/.test(text) && !text.includes('[data-theme="dark"]'),
      'Unsupported canonical light SVG variables.');
  }
  text = removeDarkRules(text);
  assert(svgSemanticBytes(original) === svgSemanticBytes(text), 'SVG transformation changed authored content.');
  return text;
}

function checkHtml(text) {
  assert(/<html\b[^>]*data-theme="light"[^>]*data-frag-archify-light="true"/.test(text), 'HTML is not pinned light.');
  assert(!text.includes("localStorage.getItem('archify-theme')"), 'Saved theme preference remains active.');
  assert(!text.includes("window.matchMedia('(prefers-color-scheme: light)')"), 'OS theme resolution remains active.');
  assert(!text.includes('serializeSvg(1, { autoTheme: true })'), 'Dual-theme SVG export remains active.');
  assert(!/ else if \(e\.key === 't' \|\| e\.key === 'T'\)/.test(text), 'Theme shortcut remains active.');
  assert(text.includes('btn.disabled = true;') && text.includes("btn.style.display = 'none';"), 'Theme button is not disabled and hidden.');
}

function checkSvg(text) {
  assert(/<svg\b[^>]*data-theme="light"[^>]*data-frag-archify-light="true"/.test(text), 'SVG is not pinned light.');
  assert(!text.includes('prefers-color-scheme') && !text.includes('[data-theme="dark"]'), 'SVG retains dark theme styling.');
  assert(!/<script\b|javascript:/i.test(text), 'SVG includes executable content.');
}

const args = process.argv.slice(2);
const checkOnly = args.includes('--check');
assert(args.every(arg=>arg==='--check'), 'Use --check or no arguments.');
const selectedDiagrams = diagrams;
const receiptPath = 'local/archify-light-transform.json';
let priorReceipt = null;
try { priorReceipt = JSON.parse(readFileSync(receiptPath, 'utf8')); } catch {}
const artifacts = selectedDiagrams.flatMap((diagram) => ['html', 'svg'].filter(extension => extension === 'html' || existsSync(`docs/architecture/${diagram}.svg`)).map((extension) => {
  const path = `docs/architecture/${diagram}.${extension}`;
  const original = readFileSync(path, 'utf8');
  const output = checkOnly ? original : extension === 'html' ? lightHtml(original) : lightSvg(original);
  (extension === 'html' ? checkHtml : checkSvg)(output);
  return { path, original, output, sourceSha256: hash(readFileSync(`docs/architecture/${diagram}.json`)),
    upstreamGeneratedSha256: hash(original), finalLightSha256: hash(output), changed: original !== output };
}));

// Preflight all artifacts before any mutation; source JSON and Archify's package stay unchanged.
if (!checkOnly) {
  for (const artifact of artifacts) if (artifact.changed) writeFileSync(artifact.path, artifact.output);
  mkdirSync(dirname(receiptPath), { recursive: true });
  const previous = artifacts.every((artifact) => !artifact.changed) ? priorReceipt : null;
  if (!previous) writeFileSync(receiptPath, JSON.stringify({
    schemaVersion: 1, transformation: 'project light-only output derived from Archify 2.17',
    upstreamArchifyDelivery: 'Unchanged historical receipt; it does not attest the transformed final bytes.',
    sourceJsonChanged: false, authoredSvgContentChanged: false,
    artifacts: artifacts.map(({ original, output, ...receipt }) => {
      const prior = priorReceipt?.artifacts.find((item) => item.path === receipt.path);
      return { ...receipt, upstreamGeneratedSha256: prior?.finalLightSha256 === receipt.upstreamGeneratedSha256
        ? prior.upstreamGeneratedSha256 : receipt.upstreamGeneratedSha256, finalBytes: Buffer.byteLength(output) };
    }),
  }, null, 2) + '\n');
}
console.log(`Architecture light-only ${checkOnly ? 'check' : 'transformation'} passed: ${artifacts.filter(a=>a.path.endsWith('.html')).length} HTML viewers and ${artifacts.filter(a=>a.path.endsWith('.svg')).length} SVGs.`);
