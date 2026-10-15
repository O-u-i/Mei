/*
 * Mei-specific Light-only Archify post-processing.
 * Adapted from the owner-provided FRAG-harness transform with explicit
 * permission to reuse it under Mei's MIT license (2026-10-16).
 */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname } from 'node:path';
import { resolve, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repo = resolve(fileURLToPath(new URL('..', import.meta.url)));
process.chdir(repo);
const diagrams = [['memory-v2.workflow', 'memory-v2.workflow'], ['memory-read.architecture', 'memory-read'],
  ['memory-promotion.workflow', 'memory-promotion'], ['mei-continuity.architecture', 'mei-continuity']]
  .filter(([source]) => existsSync(`docs/architecture/${source}.json`));
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
  let text = replaceOnce(original, /<html lang="(en|zh-CN)" data-theme="dark" data-preset="classic">/,
    (_, locale) => `<html lang="${locale}" data-theme="light" data-preset="classic" ${marker}>`, 'HTML root');
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
  text = replaceOnce(text, /<span id="theme-label">(深色|Dark)<\/span>/,
    (_, label) => `<span id="theme-label">${label === 'Dark' ? 'Light' : '浅色'}</span>`, 'initial theme label');
  text = replaceOnce(text, / else if \(e\.key === 't' \|\| e\.key === 'T'\) \{\s*e\.preventDefault\(\);\s*Archify\.theme\.toggle\(\);\s*\}/,
    '', 'theme shortcut');
  text = replaceOnce(text, /serializeSvg\(1, \{ autoTheme: true \}\)/,
    'serializeSvg(1, { autoTheme: false })', 'canonical SVG export');
  text = removeDarkRules(text);
  assert(htmlSvgBytes(original) === htmlSvgBytes(text), 'HTML transformation changed authored SVG bytes.');
  return text;
}

function readableHtml(html, type) {
  if (html.includes('data-mei-readable="2"')) return html;
  return html.replace(/<svg\b[\s\S]*?<\/svg>/g, (svg) => svg.replace('<svg ', '<svg data-mei-readable="2" ')
    .replace(/<text\b[^>]*>/g, (tag) => {
      let output = tag.replace(/font-size="([\d.]+)"/, (_, size) => `font-size="${Number(size) * 2}"`);
      if (tag.includes('data-node-label') && type === 'workflow') {
        output = output.replace(/y="([\d.]+)"/, (_, y) => `y="${Number(y) + 33}"`);
      } else if (type === 'architecture' && tag.includes('data-node-label')) {
        output = output.replace(/y="([\d.]+)"/, (_, y) => `y="${Number(y) - 6}"`);
      } else if (type === 'architecture' && tag.includes('data-detail="context"')) {
        output = output.replace(/y="([\d.]+)"/, (_, y) => `y="${Number(y) + 8}"`);
      }
      return output;
    })
    .replace(/(<g\b[^>]*data-detail="context"[^>]*data-edge-from[^>]*>\s*)<rect\b([^>]*)\/>/g,
      (all, prefix, attrs) => {
        const n = (key) => Number(attrs.match(new RegExp(`${key}="([\\d.-]+)"`))?.[1]);
        const x = n('x'), y = n('y'), width = n('width'), height = n('height');
        if (![x,y,width,height].every(Number.isFinite)) throw new Error('Unsupported edge label mask.');
        const resized = attrs.replace(/x="[^"]+"/, `x="${x - width / 2}"`)
          .replace(/y="[^"]+"/, `y="${y - height / 2}"`)
          .replace(/width="[^"]+"/, `width="${width * 2}"`).replace(/height="[^"]+"/, `height="${height * 2}"`);
        return `${prefix}<rect${resized}/>`;
      }));
}

function extractSvg(html) {
  const svgs = [...html.matchAll(/<svg\b[\s\S]*?<\/svg>/g)];
  assert(svgs.length === 1, 'Expected exactly one diagram SVG.');
  const styles = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)].map(match => match[1]).join('\n');
  assert(styles && !styles.includes(']]>'), 'Unsupported embedded SVG style.');
  let svg = svgs[0][0].replace(/<svg\b[^>]*>/, root => root.replace(/\s(?:width|height|xmlns)="[^"]*"/g, '')
    .replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" width="2048" height="1320" '));
  svg = svg.replace('>', `><style><![CDATA[${styles}]]></style>`);
  return lightSvg('<?xml version="1.0" encoding="UTF-8"?>\n' + svg + '\n');
}

function checkedCommand(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8', windowsHide: true, maxBuffer: 8 * 1024 * 1024 });
  assert(result.status === 0, `${command} ${args.join(' ')} failed:\n${result.stdout || ''}\n${result.stderr || ''}`);
  return result.stdout;
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
  assert(text.includes('<metadata id="mei-license-notices">'), 'SVG-export license notices are missing.');
}

function checkSvg(text) {
  assert(/<svg\b[^>]*data-theme="light"[^>]*data-frag-archify-light="true"/.test(text), 'SVG is not pinned light.');
  assert(!text.includes('prefers-color-scheme') && !text.includes('[data-theme="dark"]'), 'SVG retains dark theme styling.');
  assert(!/<script\b|javascript:/i.test(text), 'SVG includes executable content.');
  assert(text.includes('<metadata id="mei-license-notices">'), 'SVG license notices are missing.');
}

const args = process.argv.slice(2);
const checkOnly = args.includes('--check');
const build = args.includes('--build');
const rootIndex = args.indexOf('--archify-root');
const skill = rootIndex >= 0 && args[rootIndex + 1] ? resolve(args[rootIndex + 1]) : null;
assert(args.every((arg, i) => ['--check', '--build', '--archify-root'].includes(arg) || i === rootIndex + 1),
  'Use --build --archify-root PATH or --check.');
assert(checkOnly !== build, 'Choose exactly one of --build or --check.');
const receiptPath = 'local/archify-light-transform.json';
const transformerSha256 = hash(readFileSync(fileURLToPath(import.meta.url)));

function dimensions(text, label) {
  const svg = text.match(/<svg\b[^>]*>/)?.[0] || '';
  assert(/viewBox="0 0 2048 1320"/.test(svg), `${label}: expected 2048x1320 viewBox.`);
}

if (checkOnly) {
  const receipt = JSON.parse(readFileSync(receiptPath, 'utf8'));
  assert(receipt.transformerSha256 === transformerSha256, 'Transformer changed; rebuild diagrams.');
  for (const [source, output] of diagrams) {
    const item = receipt.diagrams.find(item => item.output === output);
    assert(item && item.specificationSha256 === hash(readFileSync(`docs/architecture/${source}.json`)), `${source}: source changed; rebuild.`);
    for (const extension of ['html', 'svg']) {
      const path = `docs/architecture/${output}.${extension}`;
      const text = readFileSync(path, 'utf8');
      (extension === 'html' ? checkHtml : checkSvg)(text);
      dimensions(text, path);
      assert(text.includes('data-mei-readable="2"'), `${path}: readable typography marker missing.`);
      assert(hash(text) === item.artifacts[extension].sha256, `${path}: final artifact hash changed.`);
    }
    assert(item.finalCheck.ok && item.finalCheck.checks.length === 9 && item.finalCheck.checks.every(check => check.ok),
      `${output}: final nine-check receipt is missing.`);
  }
  console.log(`Light/readability/source-hash checks passed for ${diagrams.length} HTML/SVG pairs.`);
} else {
  assert(skill, '--build requires an explicit --archify-root PATH.');
  const packageInfo = JSON.parse(readFileSync(join(skill, 'package.json'), 'utf8'));
  assert(packageInfo.version === '2.17.0-dev.1', 'Unreviewed Archify version; stop for license and renderer-contract review.');
  const notices = readFileSync('THIRD_PARTY_NOTICES.md', 'utf8');
  const ownLicense = readFileSync('LICENSE', 'utf8');
  assert(notices.includes('Copyright (c) 2026 tt-a1i') && notices.includes('SIL OPEN FONT LICENSE Version 1.1'), 'Approved notices are missing.');
  const licenseText = ownLicense + '\n' + notices;
  assert(!licenseText.includes('</script'), 'Unsupported notice content.');
  const scratch = resolve('local/archify-candidates');
  mkdirSync(scratch, { recursive: true });
  const pending = [];
  const receipts = [];
  for (const [source, output] of diagrams) {
    const sourcePath = resolve(`docs/architecture/${source}.json`);
    const spec = JSON.parse(readFileSync(sourcePath, 'utf8'));
    assert(spec.meta.quality_profile === 'standard' && String(spec.meta.viewBox) === '2048,1320', `${source}: expected approved standard/2048x1320 source.`);
    const rawPath = join(scratch, `${output}.upstream.html`);
    // The upstream validate/deliver wrappers recursively remove scratch trees.
    // Use the same renderer and nine-check CLI with retained, explicit files instead.
    checkedCommand(process.execPath, [join(skill, 'bin/archify.mjs'), 'render', spec.diagram_type, sourcePath, rawPath, '--quality', 'standard']);
    const upstream = JSON.parse(checkedCommand(process.execPath, [join(skill, 'scripts/check-render-output.mjs'), rawPath]));
    let html = readableHtml(lightHtml(readFileSync(rawPath, 'utf8')), spec.diagram_type);
    const escaped = licenseText.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // Keep notices inside the SVG too, so reader-triggered exports retain them.
    html = html.replace('</svg>', `<metadata id="mei-license-notices">${escaped}</metadata></svg>`);
    html = html.replace('</body>', `<script type="text/plain" id="mei-license-source-notices">\n${licenseText}\n</script>\n</body>`);
    checkHtml(html); dimensions(html, output);
    let svg = extractSvg(html);
    checkSvg(svg); dimensions(svg, output);
    const candidate = join(scratch, `${output}.final.html`);
    writeFileSync(candidate, html);
    writeFileSync(join(scratch, `${output}.final.svg`), svg);
    const finalCheck = JSON.parse(checkedCommand(process.execPath, [join(skill, 'scripts/check-render-output.mjs'), candidate]));
    assert(finalCheck.checks.length === 9 && finalCheck.checks.every(check => check.ok), `${output}: incomplete artifact validation.`);
    pending.push([`docs/architecture/${output}.html`, html], [`docs/architecture/${output}.svg`, svg]);
    receipts.push({ output, specificationSha256: hash(readFileSync(sourcePath)), upstreamCheck: upstream,
      finalCheck, artifacts: { html: { sha256: hash(html), bytes: Buffer.byteLength(html) },
        svg: { sha256: hash(svg), bytes: Buffer.byteLength(svg) } } });
  }
  // Validate every SVG as XML before replacing any last-good artifact.
  checkedCommand(process.env.PYTHON || 'python', ['-X', 'utf8', '-B', '-c',
    'import sys,xml.etree.ElementTree as E; [E.parse(path) for path in sys.argv[1:]]',
    ...diagrams.map(([, output]) => join(scratch, `${output}.final.svg`))]);
  for (const [path, content] of pending) writeFileSync(path, content);
  const receipt = { schemaVersion: 2, transformation: 'Light-only, 2x SVG typography, self-contained SVG, approved notices',
    acceptance: 'Upstream render + native nine-check CLI; NOT an upstream deliver/showcase receipt. No recursive cleanup.',
    upstreamVersion: packageInfo.version, transformerSha256, diagrams: receipts };
  mkdirSync(dirname(receiptPath), { recursive: true });
  writeFileSync(receiptPath, JSON.stringify(receipt, null, 2) + '\n');
  console.log(`Built ${diagrams.length} Light 2048x1320 HTML/SVG pairs; final native checks passed.`);
  for (const item of receipts) console.log(`${item.output}: 9/9 standard; ${item.finalCheck.composition.summary.errors} errors, ${item.finalCheck.composition.summary.warnings} warnings; ${item.artifacts.html.sha256}`);
}
