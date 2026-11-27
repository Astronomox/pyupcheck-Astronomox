"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const path = require("path");
const lib = require("../lib");

const fixture = fs.readFileSync(path.join(__dirname, "fixtures", "check-all.json"), "utf8");

test("findings from real check-all output", () => {
  const found = lib.parseFindings(fixture, "/project");
  assert.strictEqual(found.length, 6);
  const nb = found.filter((f) => f.cell != null);
  assert.deepStrictEqual(nb.map((f) => [f.cell, f.cellLine]), [[3, 1], [3, 2], [5, 0], [5, 1]]);
  const py = found.find((f) => f.file === "/project/app.py" && f.line === 1);
  assert.strictEqual(py.severity, 0);
  assert.match(py.message, /^numpy 1\.26\.4 → 2\.5\.5: /);
  assert.ok(!py.message.includes("``"), "reST markup is cleaned");
});

test("replacement is added once", () => {
  const json = JSON.stringify({ results: [{ package: "flask", current_version: "2.2.5", target_version: "3.0.0",
    risks: [
      { file: "a.py", line: 3, severity: "breaking", change_description: "Removed. :pr:`1`", replacement: "markupsafe.Markup" },
      { file: "a.py", line: 4, severity: "breaking", change_description: "Use ``markupsafe.Markup`` instead.", replacement: "markupsafe.Markup" },
    ] }] });
  const [a, b] = lib.parseFindings(json, "/root");
  assert.strictEqual(a.file, path.join("/root", "a.py"));
  assert.strictEqual(a.message, "flask 2.2.5 → 3.0.0: Removed. Use markupsafe.Markup instead.");
  assert.strictEqual(b.message.match(/markupsafe\.Markup/g).length, 1);
});

test("warnings only when asked, duplicates dropped", () => {
  const risk = { file: "/p/a.py", line: 1, severity: "warning", change_description: "mentioned" };
  const json = JSON.stringify({ results: [{ package: "x", target_version: "2", risks: [risk, risk] }] });
  assert.strictEqual(lib.parseFindings(json, "/p").length, 0);
  assert.strictEqual(lib.parseFindings(json, "/p", { includeWarnings: true }).length, 1);
});

test("bad output is an error, not silence", () => {
  assert.throws(() => lib.parseFindings("Traceback ...", "/p"), /did not return JSON/);
});

test("summary", () => {
  assert.strictEqual(lib.summary([{ severity: 0 }, { severity: 1 }]).text, "$(error) pyupcheck: 1 breaking");
  assert.strictEqual(lib.summary([{ severity: 1 }]).level, "warning");
  assert.strictEqual(lib.summary([]).level, "ok");
});

test("arguments", () => {
  assert.deepStrictEqual(lib.buildArgs({ root: "/p", deep: true, minSeverity: "deprecated" }),
    ["check-all", "--dir", "/p", "--format", "json", "--fail-on", "never", "--quiet", "--deep", "--min-severity", "deprecated"]);
  assert.ok(!lib.buildArgs({ root: "/p", minSeverity: "warning" }).includes("--min-severity"));
});

test("migrate arguments: no shell, and only real names and versions", () => {
  assert.deepStrictEqual(lib.migrateArgs("/my project", { package: "numpy", target: "2.0.0" }, false),
    ["migrate", "numpy", "2.0.0", "--dir", "/my project"]);
  assert.deepStrictEqual(lib.migrateArgs("/p", { package: "Flask_SQLAlchemy", target: "3.1.0rc1" }, true),
    ["migrate", "Flask_SQLAlchemy", "3.1.0rc1", "--dir", "/p", "--write"]);
  for (const bad of [{ package: "x;rm -rf ~", target: "1" }, { package: "--help", target: "1" },
    { package: "numpy", target: "$(id)" }, { package: "numpy", target: "1 --dir /etc" },
    { package: "a`b`", target: "1" }, { package: "numpy\n", target: "1" }]) {
    assert.strictEqual(lib.migrateArgs("/p", bad, false), null, JSON.stringify(bad));
  }
  assert.strictEqual(lib.migrateArgs("relative/dir", { package: "numpy", target: "2" }, false), null);
});

test("dependency files", () => {
  for (const f of ["requirements.txt", "requirements-dev.in", "/a/pyproject.toml", "uv.lock", "pixi.lock",
    "conda-lock.yml", "environment.yaml", "pylock.dev.toml", "Pipfile.lock"]) {
    assert.ok(lib.isDependencyFile(f), f);
  }
  for (const f of ["app.py", "README.md", "package.json", "lock.txt"]) assert.ok(!lib.isDependencyFile(f), f);
});

test("control and bidi characters are removed from messages", () => {
  assert.strictEqual(lib.cleanNote("Removed \u001b[31mx\u202e evil\u0007"), "Removed [31mx evil");
});
