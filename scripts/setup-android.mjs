#!/usr/bin/env node
/**
 * Idempotent Android setup for Training OS. Safe to re-run at any time.
 *
 *   node scripts/setup-android.mjs             full setup (adds android/ if missing)
 *   node scripts/setup-android.mjs --sync-only after editing www/index.html
 *
 * Steps
 *   1. Copy the Capacitor core IIFE into www/vendor/ (the app has no bundler).
 *   2. Create the native project with `npx cap add android` if it doesn't exist.
 *   3. Copy the alert sound into res/raw (notification channels need a sound file).
 *   4. Add USE_EXACT_ALARM to the manifest (install-time exact-alarm access);
 *      raise minSdk to 26 for the Health Connect plugin.
 *   4d. Sign debug builds with the repository's keystore so updates install over each other.
 *   5. `npx cap sync android` to copy web assets and register plugins.
 */
import { existsSync, mkdirSync, copyFileSync, readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const syncOnly = process.argv.includes('--sync-only');
const run = cmd => { console.log(`\n> ${cmd}`); execSync(cmd, { cwd: root, stdio: 'inherit', shell: true }); };
const step = msg => console.log(`\n== ${msg}`);

step('1. Vendor Capacitor core');
const coreSrc = join(root, 'node_modules/@capacitor/core/dist/capacitor.js');
if (!existsSync(coreSrc)) { console.error('node_modules missing: run `npm install` first.'); process.exit(1); }
mkdirSync(join(root, 'www/vendor'), { recursive: true });
copyFileSync(coreSrc, join(root, 'www/vendor/capacitor.js'));
console.log('   www/vendor/capacitor.js');

if (!syncOnly && !existsSync(join(root, 'android'))) {
  step('2. Create native project');
  run('npx cap add android');
}
if (!existsSync(join(root, 'android'))) { console.error('android/ missing: run without --sync-only first.'); process.exit(1); }

step('3. Alert sound');
const raw = join(root, 'android/app/src/main/res/raw');
mkdirSync(raw, { recursive: true });
copyFileSync(join(root, 'resources/rest_over.wav'), join(raw, 'rest_over.wav'));
console.log('   res/raw/rest_over.wav');

step('4. Manifest permissions');
const manifestPath = join(root, 'android/app/src/main/AndroidManifest.xml');
let manifest = readFileSync(manifestPath, 'utf8');
const perm = '<uses-permission android:name="android.permission.USE_EXACT_ALARM" />';
if (!manifest.includes('android.permission.USE_EXACT_ALARM')) {
  manifest = manifest.replace('</manifest>', `    ${perm}\n</manifest>`);
  writeFileSync(manifestPath, manifest);
  console.log('   added USE_EXACT_ALARM');
} else console.log('   USE_EXACT_ALARM already present');

step('4b. minSdk 26 (Health Connect plugin requirement)');
const varsPath = join(root, 'android/variables.gradle');
let vars = readFileSync(varsPath, 'utf8');
if (/minSdkVersion\s*=\s*(\d+)/.test(vars) && Number(vars.match(/minSdkVersion\s*=\s*(\d+)/)[1]) < 26) {
  vars = vars.replace(/minSdkVersion\s*=\s*\d+/, 'minSdkVersion = 26');
  writeFileSync(varsPath, vars);
  console.log('   minSdkVersion -> 26');
} else console.log('   minSdkVersion already >= 26');

step('4c. App icon and splash (resources/android/res, made by scripts/make_icons.py)');
const copyTree = (src, dst) => {
  for (const name of readdirSync(src)) {
    const a = join(src, name), b = join(dst, name);
    if (statSync(a).isDirectory()) { mkdirSync(b, { recursive: true }); copyTree(a, b); } else copyFileSync(a, b);
  }
};
copyTree(join(root, 'resources/android/res'), join(root, 'android/app/src/main/res'));
console.log('   icons, round icons, adaptive foreground, background colour, splash');

step('4d. Stable debug signing key (resources/training-os-debug.keystore)');
// Android refuses to install an update signed with a different key, and the default debug key is
// per-machine. A key kept in the repository means every rebuild installs over the last one, keeping data.
const gradlePath = join(root, 'android/app/build.gradle');
let gradle = readFileSync(gradlePath, 'utf8');
if (!gradle.includes('training-os-debug.keystore')) {
  const signing = `
    signingConfigs {
        debug {
            storeFile file('../../resources/training-os-debug.keystore')
            storePassword 'android'
            keyAlias 'androiddebugkey'
            keyPassword 'android'
            enableV1Signing true      // JAR signature too: some package installers can't parse v2-only APKs
            enableV2Signing true
            enableV3Signing true
        }
    }`;
  gradle = gradle.replace(/android\s*\{/, m => m + signing);
  gradle = gradle.replace(/buildTypes\s*\{/, m => m + `
        debug {
            signingConfig signingConfigs.debug
        }`);
  writeFileSync(gradlePath, gradle);
  console.log('   debug builds signed with the repository key');
} else if (!gradle.includes('enableV1Signing')) {
  writeFileSync(gradlePath, gradle.replace("keyPassword 'android'", "keyPassword 'android'\n            enableV1Signing true\n            enableV2Signing true\n            enableV3Signing true"));
  console.log('   signing already configured; added v1 + v3 signatures');
} else console.log('   signing already configured');

step('4e. Version from package.json');
{
  const ver = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version;
  const code = ver.split('.').reduce((a, v) => a * 100 + Number(v), 0);
  let g = readFileSync(gradlePath, 'utf8');
  g = g.replace(/versionCode\s+\d+/, `versionCode ${code}`).replace(/versionName\s+"[^"]*"/, `versionName "${ver}"`);
  writeFileSync(gradlePath, g);
  console.log(`   versionName ${ver}, versionCode ${code}`);
}

step('5. Sync');
run('npx cap sync android');
console.log('\nDone. Next: `npm run open` (Android Studio) or `npm run run` (install on a USB device).');

