#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <DHT.h>
#include <BH1750.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include "config.h"
#include "control.h"

DHT dht(DHT_PIN, DHT22);
BH1750 lightMeter;
bool lightReady=false;
Preferences prefs;
SemaphoreHandle_t stateMutex;
Policy policy;
Override manual[3];
Inputs inputs;
Outputs outputs;
float humidity=NAN;
int soilRaw=0;
uint32_t sampleAt=0, revision=0;
bool sampled=false;

void relay(int pin, bool on) {
  digitalWrite(pin, RELAY_ACTIVE_LOW ? !on : on);
}

bool parsePolicy(JsonVariantConst j, Policy &p) {
  if (!j.is<JsonObjectConst>() || !j["auto_enabled"].is<bool>()) return false;
  const char *fields[]={"soil_on","soil_off","temp_on","temp_off","lux_on","lux_off","pump_max_s","pump_cooldown_s"};
  for (auto key: fields) if (j[key].isNull()) return false;
  p.autoEnabled=j["auto_enabled"].as<bool>();
  p.soilOn=j["soil_on"]; p.soilOff=j["soil_off"];
  p.tempOn=j["temp_on"]; p.tempOff=j["temp_off"];
  p.luxOn=j["lux_on"]; p.luxOff=j["lux_off"];
  p.pumpMaxS=j["pump_max_s"]; p.cooldownS=j["pump_cooldown_s"];
  return validPolicy(p);
}

// Independent task: network requests cannot block the pump deadline or float input.
void controlTask(void *) {
  Controller engine;
  for (;;) {
    uint32_t now=millis();
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    Inputs s=inputs;
    if (!sampled || uint32_t(now-sampleAt)>10000) s.soil=s.temperature=s.lux=NAN;
    s.tankOk=digitalRead(TANK_PIN)==LOW;
    inputs.tankOk=s.tankOk;
    outputs=engine.step(now,s,policy,manual);
    relay(PUMP_PIN,outputs.pump); relay(FAN_PIN,outputs.fan); relay(LIGHT_PIN,outputs.light);
    xSemaphoreGive(stateMutex);
    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

void readSensors() {
  float t=dht.readTemperature(), h=dht.readHumidity();
  float lux=lightReady ? lightMeter.readLightLevel() : NAN;
  if (!isfinite(lux) || lux<0 || lux>200000) lux=NAN;
  int raw=0;
  for (int i=0;i<16;i++) { raw+=analogRead(SOIL_PIN); delay(2); }
  raw/=16;
  // Near-rail readings mean probable short/disconnection. Floating middle values
  // cannot be detected in software; use the documented 1M pulldown and inspect wiring.
  float soil=NAN;
  if (raw>100 && raw<4000 && abs(SOIL_DRY_RAW-SOIL_WET_RAW)>100)
    soil=constrain(100.0f*(SOIL_DRY_RAW-raw)/(SOIL_DRY_RAW-SOIL_WET_RAW),0.0f,100.0f);
  xSemaphoreTake(stateMutex,portMAX_DELAY);
  inputs.soil=soil; inputs.temperature=isfinite(t)&&t>=-40&&t<=85?t:NAN; inputs.lux=lux;
  humidity=isfinite(h)&&h>=0&&h<=100?h:NAN;
  soilRaw=raw; sampleAt=millis(); sampled=true;
  xSemaphoreGive(stateMutex);
  Serial.printf("raw=%d soil=%.1f temp=%.1f humidity=%.1f lux=%.1f\n",raw,soil,t,h,lux);
}

void jsonFloat(JsonDocument &doc, const char *key, float val) {
  if (isfinite(val)) doc[key]=val; else doc[key]=nullptr;
}

void sendTelemetry() {
  StaticJsonDocument<1536> doc;
  uint32_t now=millis();
  xSemaphoreTake(stateMutex,portMAX_DELAY);
  bool stale=!sampled || uint32_t(now-sampleAt)>10000;
  doc["device_id"]=DEVICE_ID; doc["uptime_s"]=now/1000;
  jsonFloat(doc,"temperature",stale?NAN:inputs.temperature);
  jsonFloat(doc,"humidity",stale?NAN:humidity);
  jsonFloat(doc,"soil_pct",stale?NAN:inputs.soil);
  jsonFloat(doc,"lux",stale?NAN:inputs.lux);
  doc["soil_raw"]=soilRaw; doc["tank_ok"]=inputs.tankOk;
  doc["pump"]=outputs.pump; doc["fan"]=outputs.fan; doc["light"]=outputs.light;
  doc["pump_cooling"]=outputs.cooling; doc["auto_enabled"]=policy.autoEnabled;
  doc["policy_revision"]=revision;
  JsonArray faults=doc.createNestedArray("faults");
  if (!inputs.tankOk) faults.add("tank_low_or_wire_open");
  if (stale || !isfinite(inputs.soil)) faults.add("soil_invalid");
  if (stale || !isfinite(inputs.temperature) || !isfinite(humidity)) faults.add("dht_invalid");
  if (stale || !isfinite(inputs.lux)) faults.add("light_invalid");
  xSemaphoreGive(stateMutex);
  String body; serializeJson(doc,body);
  WiFiClient client; HTTPClient http;
  http.setConnectTimeout(1500); http.setTimeout(2000);
  if (!http.begin(client,String(SERVER_URL)+"/api/telemetry")) return;
  http.addHeader("X-API-Key",DEVICE_TOKEN); http.addHeader("Content-Type","application/json");
  int code=http.POST(body); Serial.printf("telemetry HTTP %d\n",code); http.end();
}

void fetchControl() {
  const uint32_t requestAt=millis();
  WiFiClient client; HTTPClient http;
  http.setConnectTimeout(1500); http.setTimeout(2000);
  if (!http.begin(client,String(SERVER_URL)+"/api/control")) return;
  http.addHeader("X-API-Key",DEVICE_TOKEN);
  int code=http.GET();
  if (code==200 && http.getSize()<=4096) {
    StaticJsonDocument<4096> doc;
    auto error=deserializeJson(doc,http.getString());
    Policy next;
    if (!error && doc["revision"].is<uint32_t>() && parsePolicy(doc["policy"],next)) {
      uint32_t incoming=doc["revision"], now=millis();
      Override nextManual[3]; const char *names[]={"pump","fan","light"};
      for(int i=0;i<3;i++) {
        JsonVariant j=doc["manual"][names[i]];
        uint32_t ttl=j["remaining_s"] | 0;
        const char *mode=j["mode"] | "auto";
        const uint32_t elapsed=now-requestAt;
        if(ttl>0 && ttl<=300 && ttl*1000>elapsed) {
          nextManual[i].mode=strcmp(mode,"on")==0?ON:(strcmp(mode,"off")==0?OFF:AUTO);
          nextManual[i].until=now+ttl*1000-elapsed;
        }
      }
      xSemaphoreTake(stateMutex,portMAX_DELAY);
      bool changed=incoming!=revision;
      policy=next; revision=incoming;
      for(int i=0;i<3;i++) manual[i]=nextManual[i];
      xSemaphoreGive(stateMutex);
      if(changed) {
        String saved; serializeJson(doc["policy"],saved);
        prefs.putString("policy",saved); prefs.putUInt("revision",incoming);
      }
    }
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  for(int pin: {PUMP_PIN,FAN_PIN,LIGHT_PIN}) { relay(pin,false); pinMode(pin,OUTPUT); }
  pinMode(TANK_PIN,INPUT_PULLUP); pinMode(SOIL_PIN,INPUT);
  analogReadResolution(12); analogSetPinAttenuation(SOIL_PIN,ADC_11db);
  stateMutex=xSemaphoreCreateMutex();
  if(!stateMutex) { Serial.println("mutex failed"); while(true) delay(1000); }
  prefs.begin("farm",false);
  StaticJsonDocument<1024> saved;
  if(!deserializeJson(saved,prefs.getString("policy","{}")) && parsePolicy(saved.as<JsonVariantConst>(),policy))
    revision=prefs.getUInt("revision",0);
  Wire.begin(21,22); Wire.setTimeOut(100);
  dht.begin(); lightReady=lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);
  if(xTaskCreatePinnedToCore(controlTask,"safety-control",4096,nullptr,3,nullptr,1)!=pdPASS) {
    Serial.println("control task failed"); while(true) delay(1000);
  }
  WiFi.mode(WIFI_STA); WiFi.setAutoReconnect(true); WiFi.begin(WIFI_SSID,WIFI_PASSWORD);
}

void loop() {
  static uint32_t lastSample=0,lastPoll=0,lastUpload=0,lastReconnect=0;
  uint32_t now=millis();
  if(uint32_t(now-lastSample)>=2500) { lastSample=now; readSensors(); }
  if(WiFi.status()!=WL_CONNECTED) {
    if(uint32_t(now-lastReconnect)>=15000) { lastReconnect=now; WiFi.reconnect(); }
    delay(10); return;
  }
  if(uint32_t(now-lastPoll)>=3000) { lastPoll=now; fetchControl(); }
  if(uint32_t(now-lastUpload)>=10000) { lastUpload=now; sendTelemetry(); }
  delay(10);
}
