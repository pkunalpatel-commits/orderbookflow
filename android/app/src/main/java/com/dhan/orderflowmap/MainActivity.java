package com.dhan.orderflowmap;

import android.annotation.SuppressLint;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.appcompat.app.AppCompatActivity;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Mobile-only: polls Dhan Market Quote REST from native code (no CORS, no PC).
 */
public class MainActivity extends AppCompatActivity {

    private static final String TAG = "DhanOFM";
    private WebView webView;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean polling = new AtomicBoolean(false);

    private String clientId = "";
    private String accessToken = "";
    private String exchangeSegment = "IDX_I";
    private String securityId = "13";
    private int pollMs = 1000;

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webView = new WebView(this);
        setContentView(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        settings.setAllowUniversalAccessFromFileURLs(true);
        settings.setAllowFileAccessFromFileURLs(true);

        webView.addJavascriptInterface(new DhanBridge(), "DhanNative");
        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                view.evaluateJavascript(
                    "window.__DHAN_NATIVE__=true;" +
                    "if(typeof onDhanNativeReady==='function') onDhanNativeReady();",
                    null
                );
            }
        });

        webView.loadUrl("file:///android_asset/index.html");
    }

    private void startPolling() {
        if (polling.getAndSet(true)) return;
        executor.execute(() -> {
            long lastVol = -1;
            double lastLtp = 0;
            while (polling.get()) {
                try {
                    String json = fetchQuote();
                    if (json != null) {
                        JSONObject root = new JSONObject(json);
                        if (!"success".equals(root.optString("status"))) {
                            injectLog("Dhan API: " + root.optString("status", "error"), "err");
                        } else {
                            JSONObject data = root.optJSONObject("data");
                            if (data != null) {
                                JSONObject seg = data.optJSONObject(exchangeSegment);
                                if (seg == null && data.keys().hasNext()) {
                                    seg = data.optJSONObject(data.keys().next());
                                }
                                if (seg != null) {
                                    JSONObject q = seg.optJSONObject(securityId);
                                    if (q == null && seg.keys().hasNext()) {
                                        q = seg.optJSONObject(seg.keys().next());
                                    }
                                    if (q != null) {
                                        double ltp = q.optDouble("last_price", 0);
                                        long vol = q.optLong("volume", 0);
                                        JSONObject depth = q.optJSONObject("depth");
                                        JSONArray buy = depth != null ? depth.optJSONArray("buy") : null;
                                        JSONArray sell = depth != null ? depth.optJSONArray("sell") : null;

                                        JSONObject outData = new JSONObject();
                                        outData.put("ltp", ltp);
                                        outData.put("volume", vol);
                                        outData.put("ltt", System.currentTimeMillis());
                                        outData.put("oi", q.optLong("oi", 0));

                                        JSONObject depthOut = new JSONObject();
                                        depthOut.put("buy", buy != null ? buy : new JSONArray());
                                        depthOut.put("sell", sell != null ? sell : new JSONArray());
                                        outData.put("depth", depthOut);

                                        if (lastVol >= 0 && vol > lastVol) {
                                            JSONObject trade = new JSONObject();
                                            trade.put("qty", vol - lastVol);
                                            trade.put("price", ltp);
                                            trade.put("side", ltp >= lastLtp ? "buy" : "sell");
                                            outData.put("last_trade", trade);
                                        }
                                        lastVol = vol;
                                        lastLtp = ltp;

                                        JSONObject msg = new JSONObject();
                                        msg.put("type", "market_data");
                                        msg.put("data", outData);
                                        injectMarketData(msg.toString());
                                    }
                                }
                            }
                        }
                    }
                } catch (Exception e) {
                    Log.e(TAG, "poll error", e);
                    injectLog("Poll error: " + e.getMessage(), "err");
                }
                try {
                    Thread.sleep(pollMs);
                } catch (InterruptedException ie) {
                    break;
                }
            }
        });
    }

    private void stopPolling() {
        polling.set(false);
    }

    private String fetchQuote() throws Exception {
        URL url = new URL("https://api.dhan.co/v2/marketfeed/quote");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(8000);
        conn.setReadTimeout(8000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json");
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("access-token", accessToken);
        conn.setRequestProperty("client-id", clientId);

        JSONObject body = new JSONObject();
        try {
            body.put(exchangeSegment, new JSONArray().put(Integer.parseInt(securityId.trim())));
        } catch (NumberFormatException nfe) {
            body.put(exchangeSegment, new JSONArray().put(securityId.trim()));
        }

        byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
        conn.setRequestProperty("Content-Length", String.valueOf(bytes.length));
        try (OutputStream os = conn.getOutputStream()) {
            os.write(bytes);
        }

        int code = conn.getResponseCode();
        BufferedReader reader = new BufferedReader(new InputStreamReader(
            code >= 200 && code < 300 ? conn.getInputStream() : conn.getErrorStream(),
            StandardCharsets.UTF_8
        ));
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) sb.append(line);
        reader.close();
        conn.disconnect();

        if (code < 200 || code >= 300) {
            injectLog("HTTP " + code + ": " + sb.toString().substring(0, Math.min(120, sb.length())), "err");
            return null;
        }
        return sb.toString();
    }

    private void injectMarketData(String json) {
        final String escaped = json
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "");
        mainHandler.post(() -> {
            if (webView != null) {
                webView.evaluateJavascript(
                    "try{ if(typeof processLiveData==='function'){ " +
                    "var m=JSON.parse('" + escaped + "'); " +
                    "if(m.data) processLiveData(m.data); " +
                    "} }catch(e){ console.error(e); }",
                    null
                );
            }
        });
    }

    private void injectLog(String msg, String level) {
        final String m = msg.replace("'", "\\'").replace("\n", " ");
        mainHandler.post(() -> {
            if (webView != null) {
                webView.evaluateJavascript(
                    "try{ if(typeof connLog==='function') connLog('" + m + "','" + level + "'); }catch(e){}",
                    null
                );
            }
        });
    }

    public class DhanBridge {
        @JavascriptInterface
        public void start(String clientIdArg, String tokenArg, String segment, String secId, int intervalMs) {
            clientId = clientIdArg != null ? clientIdArg.trim() : "";
            accessToken = tokenArg != null ? tokenArg.trim() : "";
            exchangeSegment = segment != null && !segment.isEmpty() ? segment.trim() : "IDX_I";
            securityId = secId != null && !secId.isEmpty() ? secId.trim() : "13";
            pollMs = Math.max(800, intervalMs > 0 ? intervalMs : 1000);
            if (clientId.isEmpty() || accessToken.isEmpty()) {
                injectLog("Client ID and Access Token required", "err");
                return;
            }
            injectLog("Mobile Dhan: " + exchangeSegment + ":" + securityId, "ok");
            startPolling();
        }

        @JavascriptInterface
        public void stop() {
            stopPolling();
            injectLog("Stopped mobile Dhan feed", "ok");
        }

        @JavascriptInterface
        public boolean isNative() {
            return true;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        stopPolling();
        executor.shutdownNow();
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
