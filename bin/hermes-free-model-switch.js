#!/usr/bin/env node
/**
 * hermes-free-model-switch — Hermes plugin CLI installer.
 *
 * Commands:
 *   install     Copy plugin files + scripts to ~/.hermes/
 *   uninstall   Remove installed files and restore backups
 *   status      Check installation state
 *
 * Zero dependencies — only Node.js builtins.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const HERMES_HOME = process.env.HERMES_HOME || path.join(os_homedir(), ".hermes");
const HERMES_PLUGINS = path.join(HERMES_HOME, "plugins");
const HERMES_SCRIPTS = path.join(HERMES_HOME, "scripts");

const PKG_DIR = path.resolve(__dirname, "..");

const INSTALL_DIRS = [
  { src: "plugins/user/free-model", dest: path.join(HERMES_PLUGINS, "free-model") },
];
const SCRIPT_SRC = path.join(PKG_DIR, "scripts");
const SCRIPT_DEST = HERMES_SCRIPTS;

const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED = "\x1b[31m";
const RESET = "\x1b[0m";

function os_homedir() {
  return process.env.HOME || process.env.USERPROFILE || "~";
}

function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, "");
}

function copyDir(src, dest, backup = true) {
  if (!fs.existsSync(src)) return [];
  const created = [];

  if (backup && fs.existsSync(dest)) {
    const bak = dest + ".bak." + timestamp();
    fs.renameSync(dest, bak);
    created.push({ type: "backup", from: dest, to: bak });
  }

  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      const sub = copyDir(s, d, false);
      created.push(...sub);
    } else {
      fs.copyFileSync(s, d);
      fs.chmodSync(d, 0o644);
      created.push({ type: "file", path: d });
    }
  }
  return created;
}

function copyScripts() {
  if (!fs.existsSync(SCRIPT_SRC)) return [];
  const created = [];

  if (!fs.existsSync(SCRIPT_DEST)) {
    fs.mkdirSync(SCRIPT_DEST, { recursive: true });
  }

  for (const f of fs.readdirSync(SCRIPT_SRC)) {
    const src = path.join(SCRIPT_SRC, f);
    const dest = path.join(SCRIPT_DEST, f);
    const stat = fs.statSync(src);
    if (!stat.isFile()) continue;

    if (fs.existsSync(dest)) {
      const bak = dest + ".bak." + timestamp();
      fs.renameSync(dest, bak);
      created.push({ type: "backup", from: dest, to: bak });
    }
    fs.copyFileSync(src, dest);
    fs.chmodSync(dest, 0o755);
    created.push({ type: "script", path: dest });
  }

  return created;
}

function runHermes(args) {
  const { spawnSync } = require("child_process");
  const result = spawnSync("hermes", args, {
    cwd: HERMES_HOME,
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 30000,
  });
  return {
    ok: result.status === 0,
    stdout: (result.stdout || "").toString().trim(),
    stderr: (result.stderr || "").toString().trim(),
  };
}

function cmdInstall() {
  const results = [];

  // Verify Hermes home exists
  if (!fs.existsSync(HERMES_HOME)) {
    console.error(`${RED}Error:${RESET} Hermes home not found at ${HERMES_HOME}`);
    process.exit(1);
  }

  // Install plugins
  for (const dir of INSTALL_DIRS) {
    const src = path.join(PKG_DIR, dir.src);
    if (!fs.existsSync(src)) {
      console.log(`  ${YELLOW}⚠${RESET} Plugin source not found: ${dir.src}`);
      continue;
    }
    const created = copyDir(src, dir.dest);
    results.push({ target: dir.dest, files: created });
    const fileCount = created.filter(c => c.type === "file").length;
    const bakCount = created.filter(c => c.type === "backup").length;
    console.log(`  ${GREEN}✓${RESET} Plugin: ${path.basename(dir.dest)} (${fileCount} files${bakCount ? `, ${bakCount} backups` : ""})`);
  }

  // Install scripts
  const scriptFiles = copyScripts();
  if (scriptFiles.length > 0) {
    const sc = scriptFiles.filter(s => s.type === "script").length;
    const bk = scriptFiles.filter(s => s.type === "backup").length;
    console.log(`  ${GREEN}✓${RESET} Scripts: ${sc} installed${bk ? ` (${bk} backups)` : ""}`);
    results.push({ target: SCRIPT_DEST, files: scriptFiles });
  }

  // Enable plugin via hermes CLI
  const enable = runHermes(["plugins", "enable", "free-model"]);
  if (enable.ok) {
    console.log(`  ${GREEN}✓${RESET} Plugin enabled.`);
  } else {
    // May already be enabled — that's fine
    console.log(`  ${DIM}  Plugin enable: ${enable.stdout || enable.stderr}${RESET}`);
  }

  console.log();
  console.log(`${BOLD}Install complete.${RESET} Restart the gateway:`);
  console.log(`  hermes gateway restart`);
  console.log();

  return results;
}

function cmdUninstall() {
  let removed = 0;

  for (const dir of INSTALL_DIRS) {
    const dest = dir.dest;
    if (fs.existsSync(dest)) {
      // Try to restore backup
      const baks = fs.readdirSync(path.dirname(dest))
        .filter(f => f.startsWith(path.basename(dest) + ".bak."))
        .sort()
        .reverse();

      if (baks.length > 0) {
        const latest = path.join(path.dirname(dest), baks[0]);
        fs.rmSync(dest, { recursive: true, force: true });
        fs.renameSync(latest, dest);
        console.log(`  ${GREEN}✓${RESET} Restored backup: ${path.basename(dest)}`);
      } else {
        fs.rmSync(dest, { recursive: true, force: true });
        console.log(`  ${GREEN}✓${RESET} Removed: ${path.basename(dest)}`);
      }
      removed++;
    }
  }

  if (removed === 0) {
    console.log(`  ${DIM}Nothing to uninstall.${RESET}`);
  }

  console.log();
  console.log(`${BOLD}Uninstall complete.${RESET} You may want to disable the plugin:`);
  console.log(`  hermes plugins disable free-model`);
  console.log();
}

function cmdStatus() {
  let allOk = true;

  for (const dir of INSTALL_DIRS) {
    const dest = dir.dest;
    const present = fs.existsSync(dest) && fs.existsSync(path.join(dest, "__init__.py"));
    console.log(`  ${present ? GREEN + "✓" : RED + "✗"}${RESET} ${path.basename(dest)} plugin ${present ? "installed" : "NOT installed"}`);
    if (!present) allOk = false;
  }

  // Check scripts
  const scripts = fs.readdirSync(SCRIPT_SRC);
  for (const s of scripts) {
    const dest = path.join(SCRIPT_DEST, s);
    const present = fs.existsSync(dest);
    console.log(`  ${present ? GREEN + "✓" : RED + "✗"}${RESET} ${s} ${present ? "installed" : "NOT installed"}`);
    if (!present) allOk = false;
  }

  // Check if enabled
  const status = runHermes(["plugins", "list"]);
  if (status.stdout.includes("free-model")) {
    console.log(`  ${GREEN}✓${RESET} Plugin enabled in config`);
  } else {
    console.log(`  ${YELLOW}⚠${RESET} Plugin not enabled. Run: hermes plugins enable free-model`);
  }

  console.log();
  if (allOk) {
    console.log(`${GREEN}All components installed.${RESET}`);
  } else {
    console.log(`${YELLOW}Some components missing — re-run with: hermes-free-model-switch install${RESET}`);
  }
}

function cmdHelp() {
  console.log(`
${BOLD}hermes-free-model-switch${RESET}

Usage: hermes-free-model-switch <command>

Commands:
  install     Install plugin files to ~/.hermes/
  uninstall   Remove installed files (restores backups)
  status      Check installation state
  --version   Show version
  --help      Show this help
`);
}

// === Main ===
const args = process.argv.slice(2);
const cmd = args[0] || "";

switch (cmd) {
  case "install":
    cmdInstall();
    break;
  case "uninstall":
    cmdUninstall();
    break;
  case "status":
    cmdStatus();
    break;
  case "--version":
  case "-v":
    const pkg = require(path.join(PKG_DIR, "package.json"));
    console.log(pkg.version);
    break;
  case "--help":
  case "-h":
  case "":
    cmdHelp();
    break;
  default:
    console.error(`Unknown command: ${cmd}`);
    cmdHelp();
    process.exit(1);
}
