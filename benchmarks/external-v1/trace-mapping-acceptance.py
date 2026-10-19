"""Compile original TS source, then test public behavior without npm/network."""
import glob
import subprocess

subprocess.run([
    "node", "/opt/node_modules/typescript/lib/tsc.js", "--module", "commonjs",
    "--target", "es2020", "--moduleResolution", "node", "--esModuleInterop",
    "--strict", "--skipLibCheck", "--baseUrl", "/opt/node_modules",
    "--outDir", "/scratch/compiled", *glob.glob("src/*.ts"),
], check=True)
subprocess.run(["node", "-e", r"""
const assert = require('node:assert/strict');
const {TraceMap, originalPositionFor, sourceContentFor} = require('/scratch/compiled/trace-mapping.js');
const cases = [
  [undefined, undefined, 'input.js', 'input.js'],
  ['foo/script.js.map', undefined, 'input.js', 'foo/input.js'],
  [undefined, 'foo', 'input.js', 'foo/input.js'],
  ['foo/script.js.map', 'bar', 'input.js', 'foo/bar/input.js'],
  ['foo/script.js.map', 'bar', '/input.js', 'foo/bar/input.js'],
  ['foo/script.js.map', undefined, '/input.js', '/input.js'],
  ['https://example.test/maps/script.js.map', '/src', '/input.js', 'https://example.test/src/input.js'],
  ['https://example.test/maps/script.js.map', 'https://cdn.test/src', '/input.js', 'https://cdn.test/src/input.js'],
  ['foo/script.js.map', 'bar/', '../input.js', 'foo/input.js'],
];
let failures = 0;
for (const [mapUrl, sourceRoot, source, expected] of cases) {
  try {
    const map = new TraceMap({version:3, names:[], sourceRoot, sources:[source], sourcesContent:['content'], mappings:[[[0,0,0,0]]]}, mapUrl);
    assert.deepEqual(map.resolvedSources, [expected]);
    assert.equal(originalPositionFor(map, {line:1, column:0}).source, expected);
    assert.equal(sourceContentFor(map, expected), 'content');
    assert.deepEqual(map.sources, [source]);
  } catch (error) { failures++; console.error(JSON.stringify({mapUrl,sourceRoot,source,expected})); console.error(error.message); }
}
assert.equal(failures, 0, 'source path resolution regressions');
console.log('PASS: 9 path combinations, mapping lookup, content lookup, original sources preserved');
"""], check=True)
