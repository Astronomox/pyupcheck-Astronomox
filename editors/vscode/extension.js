// pyupcheck for VS Code: shows the lines a dependency upgrade breaks, as
// diagnostics, and offers `pyupcheck migrate` as a quick fix.
"use strict";

const vscode = require("vscode");
const { execFile } = require("child_process");
const { buildArgs, parseFindings, summary, migrateArgs, isDependencyFile } = require("./lib");

const SOURCE = "pyupcheck";
let diagnostics;
let status;
let output;
const running = new Map(); // workspace folder -> child process
const timers = new Map();
let warnedMissing = false;

function config() {
  const c = vscode.workspace.getConfiguration("pyupcheck");
  return {
    executable: c.get("path") || "pyupcheck",
    deep: c.get("deep", false),
    checkOnSave: c.get("checkOnSave", "dependencies"),
    showWarnings: c.get("showWarnings", false),
    minSeverity: c.get("minSeverity", "deprecated"),
  };
}

function activate(context) {
  diagnostics = vscode.languages.createDiagnosticCollection(SOURCE);
  output = vscode.window.createOutputChannel("pyupcheck");
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = "pyupcheck.check";
  status.tooltip = "pyupcheck: check what dependency upgrades break (click to re-run)";
  context.subscriptions.push(diagnostics, output, status);

  context.subscriptions.push(
    vscode.commands.registerCommand("pyupcheck.check", () => checkAll()),
    vscode.commands.registerCommand("pyupcheck.migrate", (root, finding) => runMigrate(root, finding)),
    vscode.workspace.onDidSaveTextDocument((doc) => onSave(doc.uri)),
    vscode.workspace.onDidSaveNotebookDocument((nb) => onSave(nb.uri)),
    vscode.workspace.onDidChangeWorkspaceFolders(() => checkAll()),
    vscode.workspace.onDidOpenNotebookDocument(() => republish()),
    vscode.languages.registerCodeActionsProvider(
      [{ language: "python" }, { scheme: "vscode-notebook-cell" }],
      { provideCodeActions },
      { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] },
    ),
  );
  checkAll();
}

function deactivate() {
  for (const child of running.values()) child.kill();
}

function onSave(uri) {
  const mode = config().checkOnSave;
  if (mode === "off") return;
  const dep = isDependencyFile(uri.fsPath);
  const code = /\.(py|pyi|ipynb)$/.test(uri.fsPath);
  if (!(dep || (mode === "all" && code))) return;
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  if (!folder) return;
  // a lockfile tool writes several files: check once they're all saved
  clearTimeout(timers.get(folder.uri.fsPath));
  timers.set(folder.uri.fsPath, setTimeout(() => checkFolder(folder), 1500));
}

function checkAll() {
  for (const folder of vscode.workspace.workspaceFolders || []) checkFolder(folder);
}

const results = new Map(); // folder path -> findings

function checkFolder(folder) {
  const root = folder.uri.fsPath;
  const cfg = config();
  const previous = running.get(root);
  if (previous) previous.kill();
  status.text = "$(sync~spin) pyupcheck";
  status.show();
  const args = buildArgs({ root, deep: cfg.deep, minSeverity: cfg.showWarnings ? "warning" : cfg.minSeverity });
  output.appendLine(`$ ${cfg.executable} ${args.join(" ")}`);
  const child = execFile(cfg.executable, args, { cwd: root, maxBuffer: 64 * 1024 * 1024, timeout: 15 * 60 * 1000 },
    (err, stdout, stderr) => {
      if (running.get(root) !== child) return; // superseded by a newer run
      running.delete(root);
      if (stderr) output.appendLine(stderr.trim());
      if (err && err.code === "ENOENT") {
        status.text = "$(warning) pyupcheck not found";
        if (!warnedMissing) {
          warnedMissing = true;
          vscode.window.showWarningMessage(
            `pyupcheck was not found ("${cfg.executable}"). Install it with "pip install pyupcheck", ` +
            "or set pyupcheck.path to its location.", "Open setting").then((pick) => {
            if (pick) vscode.commands.executeCommand("workbench.action.openSettings", "pyupcheck.path");
          });
        }
        return;
      }
      if (err && err.killed) return;
      try {
        results.set(root, parseFindings(stdout, root, { includeWarnings: cfg.showWarnings }));
      } catch (e) {
        output.appendLine(String(e.message || e));
        status.text = "$(warning) pyupcheck failed";
        return;
      }
      republish();
    });
  running.set(root, child);
}

/** Put every folder's findings on the documents they belong to. */
function republish() {
  diagnostics.clear();
  const byUri = new Map();
  const all = [];
  for (const [root, findings] of results) {
    for (const f of findings) {
      all.push(f);
      const target = locate(f);
      const range = new vscode.Range(target.line, 0, target.line, Number.MAX_SAFE_INTEGER);
      const d = new vscode.Diagnostic(range, f.message,
        [vscode.DiagnosticSeverity.Error, vscode.DiagnosticSeverity.Warning,
          vscode.DiagnosticSeverity.Information][f.severity]);
      d.source = SOURCE;
      d.code = `${f.package}@${f.target}`;
      d.pyupcheckRoot = root;
      const key = target.uri.toString();
      if (!byUri.has(key)) byUri.set(key, { uri: target.uri, list: [] });
      byUri.get(key).list.push(d);
    }
  }
  for (const { uri, list } of byUri.values()) diagnostics.set(uri, list);
  const s = summary(all);
  status.text = s.text;
  status.show();
}

/** A notebook finding goes on its cell when the notebook is open. */
function locate(f) {
  if (f.cell != null) {
    const nb = vscode.workspace.notebookDocuments.find((n) => n.uri.fsPath === f.file);
    if (nb && f.cell - 1 < nb.cellCount) {
      return { uri: nb.cellAt(f.cell - 1).document.uri, line: f.cellLine || 0 };
    }
  }
  return { uri: vscode.Uri.file(f.file), line: f.line };
}

function provideCodeActions(document, range, context) {
  const actions = [];
  const seen = new Set();
  for (const d of context.diagnostics) {
    if (d.source !== SOURCE || typeof d.code !== "string") continue;
    const at = d.code.lastIndexOf("@");
    const finding = { package: d.code.slice(0, at), target: d.code.slice(at + 1) };
    if (seen.has(d.code)) continue;
    seen.add(d.code);
    const folder = vscode.workspace.getWorkspaceFolder(document.uri)
      || (vscode.workspace.workspaceFolders || [])[0];
    const root = d.pyupcheckRoot || (folder && folder.uri.fsPath);
    const action = new vscode.CodeAction(
      `Fix with pyupcheck migrate ${finding.package} ${finding.target}`, vscode.CodeActionKind.QuickFix);
    action.diagnostics = [d];
    action.command = { title: "pyupcheck migrate", command: "pyupcheck.migrate", arguments: [root, finding] };
    actions.push(action);
  }
  return actions;
}

/** Run pyupcheck (no shell: arguments are passed as a list). */
function runPyupcheck(args, cwd) {
  return new Promise((resolve) => {
    const cfg = config();
    output.appendLine(`$ ${cfg.executable} ${args.join(" ")}`);
    execFile(cfg.executable, args, { cwd, maxBuffer: 64 * 1024 * 1024, timeout: 15 * 60 * 1000 },
      (err, stdout, stderr) => resolve({ err, stdout: stdout || "", stderr: stderr || "" }));
  });
}

/**
 * The quick fix: show what `pyupcheck migrate` would change, and rewrite the
 * files only after you say so.
 */
async function runMigrate(root, finding) {
  const preview = migrateArgs(root, finding, false);
  if (!preview) {
    vscode.window.showErrorMessage("pyupcheck: not a valid package or version, so nothing was run.");
    return;
  }
  output.show(true);
  const first = await runPyupcheck(preview, root);
  output.appendLine(first.stdout.trimEnd());
  if (first.stderr) output.appendLine(first.stderr.trimEnd());
  if (first.err && first.err.code === "ENOENT") {
    vscode.window.showWarningMessage(`pyupcheck was not found ("${config().executable}").`);
    return;
  }
  const files = (first.stdout.match(/^\+\+\+ /gm) || []).length;
  if (!files) {
    vscode.window.showInformationMessage(
      `pyupcheck: nothing ${finding.package} ${finding.target} can rewrite automatically. ` +
      "What needs a manual change is in the pyupcheck output.");
    return;
  }
  const pick = await vscode.window.showWarningMessage(
    `Rewrite ${files} file${files === 1 ? "" : "s"} for ${finding.package} ${finding.target}? ` +
    "The changes are shown in the pyupcheck output.", { modal: true }, "Rewrite");
  if (pick !== "Rewrite") return;
  const second = await runPyupcheck(migrateArgs(root, finding, true), root);
  output.appendLine(second.stdout.trimEnd());
  if (second.stderr) output.appendLine(second.stderr.trimEnd());
  const folder = (vscode.workspace.workspaceFolders || []).find((f) => f.uri.fsPath === root);
  if (folder) checkFolder(folder);
}

module.exports = { activate, deactivate };
