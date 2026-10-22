import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/review/trackingNumbers.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { parseTrackingNumbers } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('numeric range expands inclusively and overlaps are deduplicated', () => {
  assert.deepEqual(parseTrackingNumbers('14-25'), Array.from({ length: 12 }, (_, index) => String(index + 14)));
  assert.deepEqual(parseTrackingNumbers('14-16, 15\n16–18, A-14'), ['14', '15', '16', '17', '18', 'A-14']);
});

test('preserves padded tracking numbers and handles whitespace and dash variants', () => {
  assert.deepEqual(parseTrackingNumbers('001 — 003, 002'), ['001', '002', '003']);
  assert.deepEqual(parseTrackingNumbers('25-25'), ['25']);
});

test('rejects reversed, excessive and unsafe ranges before expansion', () => {
  for (const input of ['25-14', '1-101', '1-100, 101', '1-999999999999999999999', ' ', 'x'.repeat(129)]) {
    assert.throws(() => parseTrackingNumbers(input), Error);
  }
  assert.equal(parseTrackingNumbers('1-100').length, 100);
  assert.equal(parseTrackingNumbers('1-100, 1-100').length, 100);
});
