# Training OS (Android)

The single-file training app (`www/index.html`, the same file as
`Capoeira_Workouts.html`) wrapped for Android with Capacitor, so rest and hold
alerts are handed to Android's alarm system and fire with the screen locked.

In a normal browser the file behaves exactly as before; every native code path
is inert unless the Capacitor bridge is present.

## Quick install (no build)

`training-os-debug.apk` is a debug-signed build of this exact source.

**Upgrading from the very first build (version 1.0):** that build was signed with a different key, so
Android won't install this one over it (some phones say "There was a problem parsing the package").
Once only: **Journal → Export** in the old app, uninstall it, install this APK, then **Import** the file.
Every build from 0.3.0 on uses the key in `resources/`, so later updates install over each other.

1. Copy it to the phone and open it. Allow "install unknown apps" for your file manager when asked.
2. Open the app → **⚙ Settings → Alerts** → **Allow alerts**.
3. Phone settings → Apps → Training OS → Battery → **Unrestricted**.
4. Tap **Test alert in 60 s**, lock the phone, and wait.

The app has its own storage, separate from the browser. Move your data across
with **Export** in the browser version and **Import** in the app.

## Watch data (Oraimo via Health Connect)

The app reads — never writes — what the Oraimo Health app puts into Health Connect:
sleep, heart rate, resting heart rate, HRV, steps and workouts.

1. In the Oraimo Health app, turn on its Health Connect sync (on Android 13 or older, install "Health Connect" from the Play Store first).
2. In Training OS: **Watch tab → Connection → Connect Health Connect**, and allow the data types.
3. "What the watch shares" lists which types actually contain data and from which app. Anything showing "none" is not being written by Oraimo, whatever the permission screen says.

Health Connect only exposes data from about 30 days before you first grant access.

## Build from source

Requirements: Node 20+, JDK 21, Android SDK (platform 36). Android Studio installs the last two.

```bash
npm ci
npm run setup      # vendors Capacitor core, creates android/, adds sound + permission, syncs
npm run run        # build and install on a USB-connected phone
```

After editing `www/index.html`: `npm run sync`, then `npm run run`.

`android/` is generated and git-ignored; `npm run setup` recreates it
identically, so the repository holds only source.

## Versions this was built and tested with

| Component | Version |
|---|---|
| Capacitor core / android / cli | 8.5.2 |
| local-notifications | 8.3.1 |
| JDK | 21 |
| capacitor-health (Health Connect) | 8.11.4 |
| compileSdk / targetSdk / minSdk | 36 / 36 / 26 |

## Testing

See `TESTING.md`. Layer 1 (72 automated tests, no phone) runs with
`python -m pytest -q tests/`. Layer 2 measures delivery on your phone.
