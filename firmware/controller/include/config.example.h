#pragma once
#define WIFI_SSID "your-2.4GHz-WiFi"
#define WIFI_PASSWORD "change-me"
#define SERVER_URL "http://192.168.1.100:8000"
#define DEVICE_TOKEN "copy-DEVICE_TOKEN-from-dot-env"
#define DEVICE_ID "farm-01"
// For a trusted isolated LAN. Do not expose plaintext HTTP to the public Internet.
constexpr int DHT_PIN=4, SOIL_PIN=34, TANK_PIN=27;
constexpr int PUMP_PIN=25, FAN_PIN=26, LIGHT_PIN=33;
// Default: discrete NPN relay driver shown in docs/WIRING.md, active HIGH.
// For a verified 3.3V-safe active-low relay module, change this to true.
constexpr bool RELAY_ACTIVE_LOW=false;
// Replace these with measurements in your own dry/wet substrate.
constexpr int SOIL_DRY_RAW=3000, SOIL_WET_RAW=1300;
// NC/wet-closed float: adequate water -> GPIO27 connected to GND.
// Broken wire -> HIGH -> tank not OK -> pump inhibited.
