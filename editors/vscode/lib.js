// Pure logic for the pyupcheck extension: no `vscode` import, so it runs
// (and is tested) under plain Node.
"use strict";

const path = require("path");

const SEVERITY = { breaking: 0, deprecated: 1, warning: 2 }; // 0 Error, 1 Warning, 2 Information

// Files whose change means the dependency versions may have changed.
const DEPENDENCY_FILES = [
  /^requirements.*\.(txt|in)$/,
  /^(pyproject\.toml|setup\.cfg|setup\.py|Pipfile|Pipfile\.lock)$/,
  /^(uv\.lock|poetry\.lock|pdm\.lock|pylock(\..+)?\.toml)$/,
  /^(environment\.ya?ml|conda-lock\.ya?ml|pixi\.lock|pixi\.toml)$/,
];

function isDependencyFile(file) {
  const name = path.basename(file);
  return DEPENDENCY_FILES.some((re) => re.test(name));
}

/** The command line for checking a project. */
function buildArgs({ root, deep, minSeverity }) {
  const args = ["check-all", "--dir", root, "--format", "json", "--fail-on", "never", "--quiet"];
  if (deep) args.push("--deep");
  if (minSeverity && minSeverity !== "warning") args.push("--min-severity", minSeverity);
  return args;
}

/** Clean release-note markup for a one-line message. */
function cleanNote(text) {
  return String(text || "")
    // release notes are untrusted: no control characters or bidirectional
    // overrides that make a message read differently from what it is
    .replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g, "")
    .replace(/\s*:(?:pr|issue|ghissue|bpo|user|cve):(?:`[^`]*`?)?/g, "")
    .replace(/:[\w-]+:`~?\.?([^`]+)`/g, "`$1`")
    .replace(/``/g, "`")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Turn `pyupcheck check-all -f json` output into findings:
 * { file, line (0-based), cell, cellLine, severity (0-2), message, package, target, replacement, code }.
 * Breaking and deprecated always; "warning" findings only when asked.
 */
function parseFindings(jsonText, root, { includeWarnings = false } = {}) {
  let data;
  try {
    data = JSON.parse(jsonText);
  } catch (e) {
    throw new Error(`pyupcheck did not return JSON: ${String(jsonText).slice(0, 200)}`);
  }
  const out = [];
  const seen = new Set();
  for (const r of data.results || []) {
    for (const risk of r.risks || []) {
      if (!(risk.severity in SEVERITY)) continue;
      if (risk.severity === "warning" && !includeWarnings) continue;
      const file = path.isAbsolute(risk.file) ? risk.file : path.join(root, risk.file);
      const note = cleanNote(risk.change_description);
      let message = `${r.package} ${r.current_version || "?"} → ${r.target_version}: ${note}`;
      if (risk.replacement && !note.includes(risk.replacement)) message += ` Use ${risk.replacement} instead.`;
      const key = `${file}|${risk.cell || ""}|${risk.line}|${message}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        file,
        line: Math.max(0, (risk.line || 1) - 1),
        cell: risk.cell == null ? null : risk.cell,
        cellLine: risk.cell_line == null ? null : Math.max(0, risk.cell_line - 1),
        severity: SEVERITY[risk.severity],
        message,
        package: r.package,
        target: r.target_version,
        replacement: risk.replacement || null,
        code: risk.code || "",
        verified: !!risk.precise,
      });
    }
  }
  return out;
}

/** Status bar text for a set of findings. */
function summary(findings) {
  const breaking = findings.filter((f) => f.severity === 0).length;
  const deprecated = findings.filter((f) => f.severity === 1).length;
  if (breaking) return { text: `$(error) pyupcheck: ${breaking} breaking`, level: "error" };
  if (deprecated) return { text: `$(warning) pyupcheck: ${deprecated} deprecated`, level: "warning" };
  return { text: "$(check) pyupcheck", level: "ok" };
}

// What a package name and a version may look like (PEP 503 / PEP 440
// characters). Both come from the project's files and from pyupcheck's
// output: anything else is refused, never passed on.
const PACKAGE = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,200}[A-Za-z0-9])?$/;
const VERSION = /^[A-Za-z0-9][A-Za-z0-9.+!_-]{0,100}$/;

/**
 * Arguments for `pyupcheck migrate` (run directly, never through a shell),
 * or null when the package or version isn't a valid one.
 */
function migrateArgs(root, finding, write) {
  if (!finding || !PACKAGE.test(String(finding.package)) || !VERSION.test(String(finding.target))) return null;
  if (typeof root !== "string" || !path.isAbsolute(root)) return null;
  const args = ["migrate", finding.package, finding.target, "--dir", root];
  if (write) args.push("--write");
  return args;
}

module.exports = { buildArgs, parseFindings, summary, migrateArgs, isDependencyFile, cleanNote, SEVERITY };
