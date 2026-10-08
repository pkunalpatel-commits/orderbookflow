# Dhan OrderFlowMap

Bookmap-style **order flow heatmap** for Indian markets using **DhanHQ** depth data  
(Nifty · Crude Oil · Nifty Options).

Based on [OrderFlowMap](https://github.com/Azhagesan-dev/OrderFlowMap) (MIT).

---

## Build Android APK (Android Studio)

### Requirements
- Android Studio Hedgehog (2023.1+) or newer  
- JDK 17  
- Android SDK 34  

### Steps

1. **Open the project**
   - Launch Android Studio  
   - **File → Open** → select the `android/` folder inside this repo  

2. **Wait for Gradle sync**  
   - First open may download Gradle 8.2 and dependencies (needs internet)

3. **Build the APK**
   - Menu: **Build → Build Bundle(s) / APK(s) → Build APK(s)**  
   - Or terminal inside `android/`:
     ```bash
     ./gradlew assembleDebug
     ```

4. **Find the APK**
   ```
   android/app/build/outputs/apk/debug/app-debug.apk
   ```

5. **Install on phone**
   ```bash
   adb install app/build/outputs/apk/debug/app-debug.apk
   ```
   Or copy the APK to the phone and open it (enable “Install from unknown sources”).

### Live data on the phone
The app is a WebView. Simulation mode works offline.  
For **Live** mode, run the Python proxy on a PC/VPS and set the WebSocket URL in the app to:

```
ws://YOUR_PC_IP:8765
```

---

## Desktop / Browser (simulation + live)

```bash
# 1. Credentials
export DHAN_CLIENT_ID="your_client_id"
export DHAN_ACCESS_TOKEN="your_access_token"

# 2. Proxy
python3 -m pip install websockets
python3 dhan_proxy_server.py
# → ws://127.0.0.1:8765

# 3. Open UI
# just open index.html in Chrome / Edge
```

### Security IDs (examples)

| Instrument        | Segment    | Security ID |
|-------------------|------------|-------------|
| Nifty 50 (spot)   | `IDX_I`    | `13`        |
| Bank Nifty        | `IDX_I`    | `25`        |
| Nifty Futures     | `NSE_FNO`  | lookup in scrip master |
| Nifty Options     | `NSE_FNO`  | lookup     |
| Crude Oil Fut     | `MCX_COMM` | lookup (5-level only) |

Scrip master: https://images.dhan.co/api-data/api-scrip-master-detailed.csv

> Full 20-level depth is available only for **NSE Equity & Derivatives**. MCX uses 5-level depth.

---

## Project layout

```
├── index.html              # OrderFlowMap UI (Dhan fields)
├── dhan_proxy_server.py    # Dhan → JSON WebSocket proxy
├── README.md
├── sample_ui.png
├── android/                # ← Open THIS folder in Android Studio
│   ├── app/
│   │   └── src/main/
│   │       ├── assets/index.html
│   │       ├── java/.../MainActivity.java
│   │       └── AndroidManifest.xml
│   ├── build.gradle
│   └── settings.gradle
└── .gitignore
```

---

## License

MIT (same as original OrderFlowMap).  
Dhan API usage is subject to Dhan’s terms.
