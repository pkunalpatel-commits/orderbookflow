# Build Dhan OrderFlowMap APK

## Prerequisites

- Android Studio Hedgehog (2023.1) or newer
- JDK 17
- Android SDK 34

## Steps

1. Copy the visualizer into assets:
   ```bash
   cp ../index.html app/src/main/assets/index.html
   ```

2. Open the `android/` folder in Android Studio.

3. Wait for Gradle sync to finish.

4. **Build → Build Bundle(s) / APK(s) → Build APK(s)**

5. The APK will appear at:
   `app/build/outputs/apk/debug/app-debug.apk`

6. Install on your phone:
   ```bash
   adb install app/build/outputs/apk/debug/app-debug.apk
   ```

## Important – Live data on phone

The app is a pure WebView. Live market data still requires the Python proxy (`dhan_proxy_server.py`) running somewhere reachable from the phone:

- Same Wi-Fi: set WebSocket URL to `ws://YOUR_PC_IP:8765`
- Public VPS: `ws://your-server:8765` (use wss:// + reverse proxy for production)

Simulation mode works fully offline.

## Icons

Replace `app/src/main/res/mipmap-*/ic_launcher.png` with your own icon if desired.
A default system icon will be used if missing.
