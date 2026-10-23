#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_camera.h"
#include "config.h"

// AI Thinker ESP32-CAM + OV2640, no microSD.
bool beginCamera() {
  camera_config_t c={};
  c.ledc_channel=LEDC_CHANNEL_0; c.ledc_timer=LEDC_TIMER_0;
  c.pin_d0=5; c.pin_d1=18; c.pin_d2=19; c.pin_d3=21;
  c.pin_d4=36; c.pin_d5=39; c.pin_d6=34; c.pin_d7=35;
  c.pin_xclk=0; c.pin_pclk=22; c.pin_vsync=25; c.pin_href=23;
  c.pin_sccb_sda=26; c.pin_sccb_scl=27; c.pin_pwdn=32; c.pin_reset=-1;
  c.xclk_freq_hz=20000000; c.pixel_format=PIXFORMAT_JPEG;
  c.frame_size=psramFound()?FRAMESIZE_SVGA:FRAMESIZE_VGA;
  c.jpeg_quality=12; c.fb_count=1;
  c.fb_location=psramFound()?CAMERA_FB_IN_PSRAM:CAMERA_FB_IN_DRAM;
  c.grab_mode=CAMERA_GRAB_WHEN_EMPTY;
  esp_err_t err=esp_camera_init(&c);
  if(err!=ESP_OK) { Serial.printf("Camera init failed 0x%x\n",err); return false; }
  return true;
}

void uploadImage() {
  // Discard buffered frame so periodic captures represent the current scene.
  camera_fb_t *old=esp_camera_fb_get();
  if(old) esp_camera_fb_return(old);
  camera_fb_t *fb=esp_camera_fb_get();
  if(!fb) { Serial.println("capture failed"); return; }
  WiFiClient client; HTTPClient http;
  http.setConnectTimeout(3000); http.setTimeout(30000);
  if(http.begin(client,String(SERVER_URL)+"/api/images")) {
    http.addHeader("X-API-Key",CAMERA_TOKEN);
    http.addHeader("Content-Type","image/jpeg");
    int code=http.POST(fb->buf,fb->len);
    Serial.printf("image upload HTTP %d bytes=%u\n",code,unsigned(fb->len));
    if(code>0) Serial.println(http.getString());
    http.end();
  }
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);
  pinMode(4,OUTPUT); digitalWrite(4,LOW); // Flash off to avoid heating.
  if(!beginCamera()) { delay(10000); ESP.restart(); }
  WiFi.mode(WIFI_STA); WiFi.setAutoReconnect(true); WiFi.begin(WIFI_SSID,WIFI_PASSWORD);
  delay(1500); // Auto-exposure warmup.
}

void loop() {
  static uint32_t last=0,reconnect=0;
  static bool first=true;
  uint32_t now=millis();
  if(WiFi.status()!=WL_CONNECTED) {
    if(uint32_t(now-reconnect)>15000) { reconnect=now; WiFi.reconnect(); }
    delay(100); return;
  }
  if(first || uint32_t(now-last)>=CAPTURE_INTERVAL_MS) { first=false; last=now; uploadImage(); }
  delay(20);
}
