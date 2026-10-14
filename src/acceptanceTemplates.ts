// Examples deliberately include behavior assertions. Users adapt module paths
// and expected behavior to their project before uploading the contract.
export const acceptanceTemplates = {
  python: {
    id: 'addition-python@1',
    kind: 'command',
    argv: ['python', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'acceptance_test.py'],
    protected_tests: {
      'tests/acceptance_test.py':
        'import unittest\nfrom src.app import add\n\nclass Acceptance(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n        self.assertEqual(add(-2, 2), 0)\n',
    },
  },
  node: {
    id: 'addition-node@1',
    kind: 'command',
    argv: ['node', '--test', 'tests/acceptance.test.mjs'],
    protected_tests: {
      'tests/acceptance.test.mjs':
        "import test from 'node:test';\nimport assert from 'node:assert/strict';\nimport { add } from '../src/add.js';\ntest('addition', () => {\n  assert.equal(add(2, 3), 5);\n  assert.equal(add(-2, 2), 0);\n});\n",
    },
  },
};

export function validateAcceptance(
  value: unknown,
): asserts value is Record<string, unknown> & { id: string; argv: string[] } {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw Error('验收 JSON 必须是一个对象。');
  const contract = value as Record<string, unknown>;
  if (typeof contract.id !== 'string' || !contract.id.trim())
    throw Error('验收 id：填写版本名称，例如 search@1。');
  if (
    !Array.isArray(contract.argv) ||
    !contract.argv.length ||
    contract.argv.some((v) => typeof v !== 'string' || !v.trim())
  )
    throw Error('验收 argv：使用非空命令数组，例如 ["python", "-m", "unittest"]。');
  const tests = contract.protected_tests;
  if (
    tests != null &&
    (typeof tests !== 'object' ||
      Array.isArray(tests) ||
      Object.values(tests).some((v) => typeof v !== 'string' || !v.trim()))
  )
    throw Error('验收 protected_tests：以相对文件路径为键、完整测试代码为值。');
  if (
    (!tests || !Object.keys(tests).length) &&
    (!Array.isArray(contract.conditions) || !contract.conditions.length)
  )
    throw Error(
      '请提供 protected_tests 独立测试或 conditions 验收条件；可以下载示例后按项目修改。',
    );
}
