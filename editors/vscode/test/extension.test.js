"use strict";
// Runs extension.js against a small stand-in for the VS Code API and a fake
// pyupcheck executable that prints a recorded check-all result.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const Module = require("module");

function fakeVscode(settings, workspaceRoot) {
  const calls = { diagnostics: new Map(), commands: new Map(), terminals: [], status: null, codeActions: null };
  class Range { constructor(l1, c1, l2, c2) { Object.assign(this, { l1, c1, l2, c2 }); } }
  class Diagnostic { constructor(range, message, severity) { Object.assign(this, { range, message, severity }); } }
  class CodeAction { constructor(title, kind) { Object.assign(this, { title, kind }); } }
  const uriFile = (p) => ({ fsPath: p, scheme: "file", toString: () => `file://${p}` });
  const disposable = { dispose() {} };
  const vscode = {
    Range, Diagnostic, CodeAction,
    Uri: { file: uriFile },
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2 },
    StatusBarAlignment: { Left: 1 },
    CodeActionKind: { QuickFix: "quickfix" },
    languages: {
      createDiagnosticCollection: () => ({
        clear: () => calls.diagnostics.clear(),
        set: (uri, list) => calls.diagnostics.set(uri.fsPath, list),
        dispose() {},
      }),
      registerCodeActionsProvider: (_sel, provider) => { calls.codeActions = provider; return disposable; },
    },
    window: {
      createOutputChannel: () => ({ appendLine() {}, dispose() {} }),
      createStatusBarItem: () => (calls.status = { show() {}, dispose() {} }),
      showWarningMessage: async () => undefined,
      createTerminal: (opts) => {
        const t = { opts, sent: [], show() {}, sendText(s) { t.sent.push(s); } };
        calls.terminals.push(t);
        return t;
      },
    },
    commands: {
      registerCommand: (name, fn) => { calls.commands.set(name, fn); return disposable; },
      executeCommand: async () => undefined,
    },
    workspace: {
      workspaceFolders: [{ uri: uriFile(workspaceRoot) }],
      notebookDocuments: [],
      getConfiguration: () => ({ get: (k, d) => (k in settings ? settings[k] : d) }),
      getWorkspaceFolder: () => ({ uri: uriFile(workspaceRoot) }),
      onDidSaveTextDocument: () => disposable,
      onDidSaveNotebookDocument: () => disposable,
      onDidChangeWorkspaceFolders: () => disposable,
      onDidOpenNotebookDocument: () => disposable,
    },
  };
  return { vscode, calls };
}

function loadExtension(vscode) {
  const original = Module._load;
  Module._load = function (request, ...rest) {
    if (request === "vscode") return vscode;
    return original.call(this, request, ...rest);
  };
  try {
    delete require.cache[require.resolve("../extension")];
    return require("../extension");
  } finally {
    Module._load = original;
  }
}

async function until(cond, ms = 5000) {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > ms) throw new Error("timed out");
    await new Promise((r) => setTimeout(r, 20));
  }
}

test("a check run becomes diagnostics, a status and a migrate quick fix", async () => {
  if (process.platform === "win32") return; // the fake executable is a shell script
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pyupcheck-vscode-"));
  const fixture = path.join(__dirname, "fixtures", "check-all.json");
  const fake = path.join(dir, "pyupcheck");
  // prints the recorded output with its paths moved under this test's folder
  fs.writeFileSync(fake, `#!/bin/sh\nsed "s|/project|${dir}|g" "${fixture}"\n`, { mode: 0o755 });

  const { vscode, calls } = fakeVscode({ path: fake }, dir);
  const ext = loadExtension(vscode);
  ext.activate({ subscriptions: [] });
  await until(() => calls.diagnostics.size > 0);

  const app = calls.diagnostics.get(path.join(dir, "app.py"));
  assert.strictEqual(app.length, 2);
  assert.strictEqual(app[0].severity, 0);
  assert.strictEqual(app[0].range.l1, 1);
  assert.strictEqual(app[0].source, "pyupcheck");
  assert.strictEqual(app[0].code, "numpy@2.5.5");
  // the notebook isn't open in an editor: its findings go on the file's lines
  assert.strictEqual(calls.diagnostics.get(path.join(dir, "analysis.ipynb")).length, 4);
  assert.strictEqual(calls.status.text, "$(error) pyupcheck: 6 breaking");

  const [action] = calls.codeActions.provideCodeActions(
    { uri: vscode.Uri.file(path.join(dir, "app.py")) }, null, { diagnostics: app });
  assert.strictEqual(action.title, "Fix with pyupcheck migrate numpy 2.5.5");
  calls.commands.get("pyupcheck.migrate")(...action.command.arguments);
  assert.deepStrictEqual(calls.terminals[0].sent, [`${fake} migrate numpy 2.5.5 --dir ${dir} --write`]);
  ext.deactivate();
});

test("a missing executable is reported, not thrown", async () => {
  const { vscode, calls } = fakeVscode({ path: "/nonexistent/pyupcheck" }, os.tmpdir());
  const ext = loadExtension(vscode);
  ext.activate({ subscriptions: [] });
  await until(() => calls.status.text === "$(warning) pyupcheck not found");
  assert.strictEqual(calls.diagnostics.size, 0);
});
