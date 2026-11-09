# Stone Launcher

Stone Launcher is a desktop Minecraft launcher built with Python and PyQt6. It provides a graphical interface for signing in with a Microsoft account, selecting supported Minecraft versions, preparing the required game files and Java runtime, and launching the game.

This README describes the source code contained in this archive. Some features depend on external services and may change independently of the launcher.

## Features

- **Microsoft account sign-in** using the Microsoft authentication flow provided by `minecraft-launcher-lib`.
- **Minecraft version selection** from a curated list in `stone_launcher_refactored/versions.py`.
- **Automatic game setup**: downloads version metadata, the client JAR, libraries, native libraries, and assets when needed.
- **Java runtime management**: checks bundled and system Java installations and can install the matching Mojang runtime.
- **Fabric support** for the regular supported-version launch path.
- **Special Minecraft 1.8.9 path** using Forge and OptiFine.
- **Launcher-managed mods** downloaded from Modrinth, with a manifest used to track launcher-managed files.
- **Optional Iris shaders** for Fabric versions.
- **Discord Rich Presence**, when Discord is running and the optional `pypresence` dependency is installed.
- **Saved account and preferences**, including the selected game version and shader toggle.
- **Update checking** for packaged/frozen builds.
- **System-tray behavior**: closing the window hides the launcher; use the tray menu's Exit action to quit fully.

## Minecraft versions

The version catalogue is defined in `stone_launcher_refactored/versions.py`.

### Main version list

- 1.21.5
- 1.20.1
- 1.19.2
- 1.18.2
- 1.17.1
- 1.16.5

### Additional / limited entries

The source also lists these separately and labels them as limited or special-path versions in the interface:

- 26.1.2 (display alias: 26.1)
- 1.21.11
- 1.8.9 (Forge + OptiFine path)

The exact availability of versions and mod builds depends on Mojang, Fabric, Forge, Modrinth, and other download endpoints.

## Requirements

- Python 3.10 or newer is recommended (the source uses modern Python type syntax).
- A working internet connection for authentication and game/runtime/mod downloads.
- A Microsoft account that owns Minecraft: Java Edition.
- Discord is optional; it is only needed for Discord Rich Presence.
- Java does not necessarily need to be installed manually: the launcher checks for a compatible runtime and can install a Mojang runtime when required.

### Python packages

The source imports these third-party packages:

- `PyQt6`
- `minecraft-launcher-lib`
- `requests`
- `Flask`
- `Werkzeug` (used by Flask's local OAuth callback server)
- `pypresence` (optional; Discord Rich Presence)

Install them with:

```bash
python -m pip install PyQt6 minecraft-launcher-lib requests Flask pypresence
```

If you do not want Discord Rich Presence, `pypresence` may be omitted. The launcher has an import fallback for that optional package.

## Running from source

1. Install Python and the packages listed above.
2. Extract this archive.
3. Open a terminal in the extracted project directory (the directory containing `StoneLauncher.py`).
4. Run:

   ```bash
   python StoneLauncher.py
   ```

   Alternatively, run the package entry point:

   ```bash
   python -m stone_launcher_refactored.main
   ```

The launcher starts a local OAuth callback server on `http://127.0.0.1:8080/` for the Microsoft sign-in flow. Make sure that port is available and that local security software does not block the callback.

> **Note:** The archive does not include a dependency lockfile, a `requirements.txt`, or a build script. The package installation command above is based on imports found in the source and is not a tested, pinned environment.

## First launch

1. Start Stone Launcher.
2. Select **Sign in with Microsoft** and complete the sign-in in the browser.
3. Choose a Minecraft version from the Versions screen.
4. Optionally enable **Add Shaders (Iris)** for the Fabric launch path.
5. Return to the Play screen and select **Launch**.

On the first launch of a version, the launcher may need to download Minecraft files, libraries, assets, Java, a mod loader, and configured mods. Later launches can reuse files already present.

## Mods and shaders

For the Fabric launch path, the launcher manages a set of mods that includes:

- Fabric API
- Entity Culling
- Sodium when shaders are disabled
- Iris and its dependencies when shaders are enabled
- WorldHost when the selected game version is within the range supported by the code
- Stone Client for Minecraft 1.20.1

Launcher-managed files are tracked in `.stone-managed.json` inside the Minecraft `mods` directory. The code is designed to remove obsolete launcher-managed mods while preserving files that were added manually by the user.

The 1.8.9 path instead installs Forge and downloads OptiFine. The shader toggle is intended for the Fabric path.

## Data locations

The application stores its data under a `StoneLauncher` directory:

- On Windows, the base is `%APPDATA%\StoneLauncher`.
- In a source run on other platforms, the fallback base is the application directory.

Within that directory:

- `.minecraft/` — Minecraft versions, libraries, assets, natives, mods, and game data.
- `.stone/account.json` — saved account session information.
- `.stone/prefs.json` — selected version and shader preference.

On Windows, the `.stone` data directory is marked hidden by the application. Treat account/session files as sensitive and do not share them.

## Project layout

```text
StoneLauncher.py
img/
  logo.ico
  logo.png
stone_launcher_refactored/
  __init__.py
  account_service.py
  config.py
  discord_presence.py
  download_manager.py
  gui.py
  launch_thread.py
  main.py
  minecraft_service.py
  oauth_server.py
  storage.py
  updater.py
  versions.py
  widgets.py
```

| File | Responsibility |
|---|---|
| `StoneLauncher.py` | Top-level source entry point |
| `main.py` | Starts the Qt application, OAuth callback server, GUI, and update check |
| `gui.py` | Main window, sign-in flow, version selection, settings, and launch controls |
| `minecraft_service.py` | Minecraft downloads, Java selection, mod management, and loader installation |
| `launch_thread.py` | Performs setup and starts Minecraft outside the GUI thread |
| `download_manager.py` | Shared HTTP download engine with retries, parallel downloads, and file validation |
| `oauth_server.py` | Local callback server for Microsoft authentication |
| `account_service.py` | Refreshes saved Microsoft account sessions |
| `storage.py` | Saves account data and launcher preferences |
| `discord_presence.py` | Discord Rich Presence integration |
| `updater.py` | Checks for and applies updates in packaged builds |
| `versions.py` | Version catalogue, aliases, and version presentation metadata |
| `widgets.py` | Reusable Qt widgets and resource-path helper |
| `config.py` | Application version, service URLs, runtime settings, and data paths |

## Troubleshooting

- **Sign-in does not return to the launcher:** check that `localhost:8080` is available and that the browser completed the Microsoft sign-in flow.
- **Downloads fail:** check your internet connection, firewall/proxy settings, and whether the relevant Mojang, Fabric, Forge, Modrinth, or configured file host is reachable.
- **A game version will not launch:** check the launcher's status message and confirm that its required Java runtime and game files were installed successfully.
- **Discord status is missing:** open the Discord desktop client and ensure `pypresence` is installed. Discord Rich Presence is optional.
- **The window appears to close but the process remains:** the normal close action hides the launcher in the system tray. Choose **Exit** from the tray menu to quit.

## Security and configuration notes

- The source configuration contains a Microsoft OAuth client secret. **Do not publish or redistribute that secret.** If this archive or source has been shared publicly, the application owner should rotate the exposed credential and move secrets out of source control. A desktop application cannot reliably keep a client secret confidential.
- `account.json` contains authentication/session data. Do not upload or share it.
- The launcher downloads and runs Minecraft components, Java, loaders, and mods from external services. Review the sources and their terms before distribution.
- The updater is designed to replace the executable in a packaged/frozen build; automatic replacement is skipped when running from Python source.

## Credits and external services

Stone Launcher integrates with or retrieves data from:

- Mojang/Minecraft version metadata and game assets
- Microsoft account authentication
- `minecraft-launcher-lib`
- Fabric and Forge
- Modrinth
- Discord Rich Presence (`pypresence`)
- The project update/version endpoint and configured file-hosting links

Minecraft is a trademark of Microsoft/Mojang. Stone Launcher is an independent project and is not presented in this source archive as an official Minecraft product.

## License

No license file was present in the supplied archive. Unless the project owner adds a license, the source should be treated as having **no explicit open-source redistribution license**. Add a `LICENSE` file before inviting others to reuse, modify, or redistribute the code.
