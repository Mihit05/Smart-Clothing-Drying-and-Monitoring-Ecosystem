#include <WiFi.h>
#include <HTTPClient.h>
#include <ESP32Servo.h>
#include <time.h>
#include "DHT.h"

// ---------------- CONFIG ----------------
const char* WIFI_SSID = " WIFI_NAME ";      
const char* WIFI_PASS = " WIFI_PASSWORD ";   

// AFTER deploying Apps Script as Web App, paste the Web App URL BELOW:
const char* SCRIPT_URL = "GOOGLE_SCRIPT_URL "; 

const char* SECRET_KEY = " SECRET_KEY_GOOGLE_SHEET";

// Moisture ADC pin & calibration
const int MOIST_PIN = 35;   // ADC1_CH7
const int ADC_BITS = 12;
const int MA_WINDOW = 8;
int ma_buf[MA_WINDOW];
int ma_idx = 0;
long ma_sum = 0;
bool ma_filled = false;
int cal_dry = 3500;   // Thresholds
int cal_wet  = 1000;  

// DHT11 config
#define DHTPIN 14      // <-- GPIO used for DHT11 DATA
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// Timing
const unsigned long SEND_INTERVAL_MS = 20000UL; // send sensors every 20s
const unsigned long POLL_INTERVAL_MS = 5000UL;  // poll sheet every 5s
unsigned long lastSend = 0;
unsigned long lastPoll = 0;

// NTP
const char* NTP_SERVER = "pool.ntp.org";
const long GMT_OFFSET_SEC = 19800; // India +5:30
const int  DAYLIGHT_OFFSET_SEC = 0;

// Servo config
const int SERVO_PIN = 32;     // signal pin for servo
Servo myServo;
const int FOLD_ANGLE = 110;   // angle for folded position
const int UNFOLD_ANGLE = 10;  // angle for unfolded/rest position
String lastServoState = "";   // "FOLD" or "UNFOLD" or ""


// ---------------- Helpers ----------------
String urlEncode(const String &str) {
  String encoded = "";
  char buf[4];
  for (size_t i = 0; i < str.length(); ++i) {
    char c = str.charAt(i);
    if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') encoded += c;
    else { sprintf(buf, "%%%02X", (unsigned char)c); encoded += buf; }
  }
  return encoded;
}

bool wifiConnect(unsigned long timeoutMs = 15000) {
  if (WiFi.status() == WL_CONNECTED) return true;
  Serial.printf("Connecting to WiFi '%s' ...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start >= timeoutMs) {
      Serial.println("\nWiFi connect timed out");
      return false;
    }
    delay(300);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected!");
  Serial.print("IP: "); Serial.println(WiFi.localIP());
  return true;
}

// NTP sync helper
bool waitForNtpSync(int maxWaitSecs = 30, const char* ntpServer = "pool.ntp.org",
                    long gmtOffsetSec = GMT_OFFSET_SEC, int daylightOffsetSec = DAYLIGHT_OFFSET_SEC) {
  configTime(gmtOffsetSec, daylightOffsetSec, ntpServer);
  Serial.printf("configTime called (server=%s). Waiting up to %d s for sync...\n", ntpServer, maxWaitSecs);
  time_t now = time(nullptr);
  int waited = 0;
  while (now < 1000000000 && waited < maxWaitSecs) {
    delay(1000);
    Serial.print(".");
    now = time(nullptr);
    waited++;
  }
  Serial.println();
  if (now >= 1000000000) {
    struct tm timeinfo;
    gmtime_r(&now, &timeinfo);
    char buf[64];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S UTC", &timeinfo);
    Serial.printf("NTP sync OK: %s\n", buf);
    return true;
  } else {
    Serial.println("NTP sync failed (timeout) - will use millis() fallback timestamps.");
    return false;
  }
}

// Simple HTTP GET with retries; returns trimmed response in 'response'
bool httpGetWithRetries(const String &url, String &response, int maxRetries = 3, int retryDelayMs = 500) {
  int attempt = 0;
  response = "";
  while (attempt < maxRetries) {
    attempt++;
    if (WiFi.status() != WL_CONNECTED) {
      if (!wifiConnect(5000)) { delay(retryDelayMs); continue; }
    }
    HTTPClient http;
    http.begin(url);
    int httpCode = http.GET();
    if (httpCode > 0) {
      response = http.getString();
      http.end();
      // trim whitespace/newlines
      while (response.length() && (response.endsWith("\n") || response.endsWith("\r") || response.endsWith(" "))) response.remove(response.length()-1);
      while (response.length() && (response.charAt(0) == '\n' || response.charAt(0) == '\r' || response.charAt(0) == ' ')) response.remove(0,1);
      Serial.printf("HTTP GET OK (code=%d). Resp len=%d\n", httpCode, (int)response.length());
      Serial.println("Raw response: '" + response + "'");
      return true;
    } else {
      Serial.printf("HTTP GET failed (code=%d). Attempt %d/%d\n", httpCode, attempt, maxRetries);
      http.end();
      delay(retryDelayMs * attempt);
    }
  }
  return false;
}

// ---------------- Sensors / Utils ----------------
int readADC_smoothed() {
  int raw = analogRead(MOIST_PIN);
  if (MA_WINDOW <= 1) return raw;
  ma_sum -= ma_buf[ma_idx];
  ma_buf[ma_idx] = raw;
  ma_sum += ma_buf[ma_idx];
  ma_idx = (ma_idx + 1) % MA_WINDOW;
  if (!ma_filled && ma_idx == 0) ma_filled = true;
  int count = ma_filled ? MA_WINDOW : ma_idx;
  return (int)(ma_sum / (count > 0 ? count : 1));
}

int adc_to_percent(int raw) {
  if (cal_wet == cal_dry) return 0;
  float pct;
  if (cal_wet < cal_dry) pct = 100.0f * (1.0f - float(raw - cal_wet) / float(cal_dry - cal_wet));
  else pct = 100.0f * (1.0f - float(raw - cal_dry) / float(cal_wet - cal_dry));
  if (pct < 0.0f) pct = 0.0f;
  if (pct > 100.0f) pct = 100.0f;
  return (int)round(pct);
}

String getISOTime() {
  time_t now = time(nullptr);
  if (now < 1000000000) { // fallback
    unsigned long ms = millis();
    char buf[32];
    sprintf(buf, "ms:%lu", ms);
    return String(buf);
  }
  struct tm timeinfo;
  gmtime_r(&now, &timeinfo);
  char buf[64];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
  return String(buf);
}

// Send moisture + DHT11 row to Apps Script (append)
bool sendMoistureRow(const String &timestamp, int raw, int pct,
                     float tempC, float humPct) {
  String url = String(SCRIPT_URL) + "?key=" + urlEncode(String(SECRET_KEY))
               + "&timestamp="    + urlEncode(timestamp)
               + "&moisture_raw=" + String(raw)
               + "&moisture_pct=" + String(pct)
               + "&temp_c="       + String(tempC, 1)
               + "&hum_pct="      + String(humPct, 1);
  String resp;
  bool ok = httpGetWithRetries(url, resp, 3, 500);
  if (ok) Serial.println("Row sent. Response: " + resp);
  else    Serial.println("Failed to send row.");
  return ok;
}

// Robust pollCommand: requests JSON and extracts "value", fallback to scanning for 0/1
bool pollCommand(String &outVal) {
  outVal = "";
  String url = String(SCRIPT_URL) + "?key=" + urlEncode(String(SECRET_KEY))
               + "&action=get_command&format=json"; // explicit JSON format
  String resp;
  if (!httpGetWithRetries(url, resp, 3, 500)) {
    Serial.println("httpGetWithRetries failed");
    return false;
  }

  Serial.println("Polled raw response: '" + resp + "'");

  // Try to parse JSON manually: find "value":"...".
  int idx = resp.indexOf("\"value\"");
  if (idx != -1) {
    int colon = resp.indexOf(":", idx);
    if (colon != -1) {
      int q1 = resp.indexOf("\"", colon);
      int q2 = resp.indexOf("\"", q1 + 1);
      if (q1 != -1 && q2 != -1 && q2 > q1 + 1) {
        String val = resp.substring(q1 + 1, q2);
        val.trim();
        Serial.println("Parsed JSON value: '" + val + "'");
        if (val == "0" || val == "1" || val == "") {
          outVal = val;
          return true;
        }
      }
    }
  }

  // Fallback: scan for first '0' or '1' anywhere
  for (size_t i = 0; i < resp.length(); ++i) {
    char c = resp.charAt(i);
    if (c == '0' || c == '1') {
      outVal = String(c);
      Serial.println("Found digit in response -> " + outVal);
      return true;
    }
  }

  Serial.println("No valid command found in response; treating as empty.");
  outVal = "";
  return true;
}

// Clear the command cell (H2) by calling action=clear_command
bool clearCommand() {
  String url = String(SCRIPT_URL) + "?key=" + urlEncode(String(SECRET_KEY))
               + "&action=clear_command";
  String resp;
  if (httpGetWithRetries(url, resp, 3, 500)) {
    Serial.println("clearCommand: success. Server response: " + resp);
    return true;
  } else {
    Serial.println("clearCommand: failed.");
    return false;
  }
}

// Execute numeric command "0" -> UNFOLD, "1" -> FOLD
void executeNumericCommand(const String &val) {
  String v = val;
  v.trim();
  if (v == "") return;

  if (v == "1") {
    if (lastServoState != "FOLD") {
      Serial.println("Executing: FOLD");
      myServo.write(FOLD_ANGLE);
      delay(600);
      lastServoState = "FOLD";
    } else Serial.println("Already FOLD; skipping.");
  } else if (v == "0") {
    if (lastServoState != "UNFOLD") {
      Serial.println("Executing: UNFOLD");
      myServo.write(UNFOLD_ANGLE);
      delay(600);
      lastServoState = "UNFOLD";
    } else Serial.println("Already UNFOLD; skipping.");
  } else {
    Serial.printf("executeNumericCommand: unexpected value '%s'\n", v.c_str());
  }
}

// ---------------- Setup & Loop ----------------
void setup() {
  Serial.begin(115200);
  while (!Serial) delay(1);
  Serial.println("ESP32: moisture + DHT11 + sheet-driven servo control starting...");

  // ADC init
  analogReadResolution(ADC_BITS);
  analogSetPinAttenuation(MOIST_PIN, ADC_11db);
  for (int i = 0; i < MA_WINDOW; ++i) ma_buf[i] = 0;

  // DHT init
  dht.begin();

  // Servo init
  myServo.setPeriodHertz(50);
  myServo.attach(SERVO_PIN);
  myServo.write(UNFOLD_ANGLE);
  lastServoState = "UNFOLD";
  delay(200);

  // WiFi connect
  if (!wifiConnect(15000)) {
    Serial.println("WiFi connect failed; will continue and retry in loop.");
  }

  // NTP (try to sync but it's optional)
  waitForNtpSync(30, NTP_SERVER, GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC);

  lastSend = millis();
  lastPoll = millis();
}

void loop() {
  unsigned long now = millis();

  // 1) Periodically send sensor row
  if (now - lastSend >= SEND_INTERVAL_MS) {
    lastSend = now;

    // Moisture
    int raw = readADC_smoothed();
    int pct = adc_to_percent(raw);

    // DHT11 temperature & humidity
    float h = dht.readHumidity();
    float t = dht.readTemperature(); // Celsius

    if (isnan(h) || isnan(t)) {
      Serial.println("Failed to read from DHT11 sensor, using -1 placeholders.");
      h = -1;
      t = -1;
    }

    String ts = getISOTime();
    Serial.printf("%s  Moisture raw=%d pct=%d  Temp=%.1fC  Hum=%.1f%%\n",
                  ts.c_str(), raw, pct, t, h);

    sendMoistureRow(ts, raw, pct, t, h);
  }

  // 2) Poll command (no-clear), execute, then clear via clear_command
  if (now - lastPoll >= POLL_INTERVAL_MS) {
    lastPoll = now;
    String cmdVal;
    bool ok = pollCommand(cmdVal);
    if (ok) {
      if (cmdVal.length() > 0) {
        Serial.println("Polled command value: '" + cmdVal + "'");
        executeNumericCommand(cmdVal);
        // After executing, clear command in sheet (ESP is responsible for clearing)
        if (!clearCommand()) {
          Serial.println("Warning: could not clear command cell; will retry next poll.");
        }
      } else {
        Serial.println("Poll: no command in sheet.");
      }
    } else {
      Serial.println("Poll failed (network or parse error).");
    }
  }

  delay(10); // small yield
}
