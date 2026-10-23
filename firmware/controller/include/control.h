#pragma once
#include <stdint.h>
#include <math.h>

// Portable control engine: no Arduino or network dependency.
struct Policy {
  bool autoEnabled = false;
  float soilOn=30, soilOff=45, tempOn=30, tempOff=27, luxOn=2000, luxOff=4000;
  uint32_t pumpMaxS=15, cooldownS=300;
};
inline bool validPolicy(const Policy &p) {
  return isfinite(p.soilOn) && isfinite(p.soilOff) && p.soilOn>=0 && p.soilOff<=100 && p.soilOn<p.soilOff
    && isfinite(p.tempOn) && isfinite(p.tempOff) && p.tempOff>=0 && p.tempOn<=60 && p.tempOff<p.tempOn
    && isfinite(p.luxOn) && isfinite(p.luxOff) && p.luxOn>=0 && p.luxOff<=150000 && p.luxOn<p.luxOff
    && p.pumpMaxS>=1 && p.pumpMaxS<=60 && p.cooldownS>=30 && p.cooldownS<=3600;
}
enum Mode { AUTO=0, ON=1, OFF=2 };
struct Override {
  Mode mode=AUTO;
  uint32_t until=0;
  Mode active(uint32_t now) {
    // Clear expired state permanently so an old override cannot revive after
    // another half-wrap while the device remains offline for weeks.
    if (mode!=AUTO && int32_t(until-now)<=0) mode=AUTO;
    return mode;
  }
};
struct Inputs {
  float soil=NAN, temperature=NAN, lux=NAN;
  bool tankOk=false;
};
struct Outputs { bool pump=false, fan=false, light=false, cooling=false; };

class Controller {
  Outputs out;
  uint32_t pumpStarted=0, pumpStopped=0;
  // Boot starts with a cooldown to avoid rebooting around the irrigation limit.
  bool stopped=true;
  static bool choose(Mode mode, bool automatic) { return mode==ON || (mode==AUTO && automatic); }
public:
  Outputs step(uint32_t now, const Inputs &s, const Policy &p, Override *manual) {
    bool cooling=stopped && uint32_t(now-pumpStopped)<p.cooldownS*1000;
    bool pumpAuto=p.autoEnabled && isfinite(s.soil) && (out.pump ? s.soil<p.soilOff : s.soil<p.soilOn);
    bool pump=choose(manual[0].active(now), pumpAuto);
    if (!s.tankOk || !isfinite(s.soil) || cooling) pump=false;
    if (out.pump && uint32_t(now-pumpStarted)>=p.pumpMaxS*1000) pump=false;
    if (pump && !out.pump) { pumpStarted=now; stopped=false; }
    if (!pump && out.pump) { pumpStopped=now; stopped=true; cooling=true; }
    out.pump=pump;
    bool fanAuto=p.autoEnabled && (!isfinite(s.temperature) || (out.fan ? s.temperature>p.tempOff : s.temperature>p.tempOn));
    bool lightAuto=p.autoEnabled && isfinite(s.lux) && (out.light ? s.lux<p.luxOff : s.lux<p.luxOn);
    out.fan=choose(manual[1].active(now), fanAuto);
    out.light=choose(manual[2].active(now), lightAuto);
    out.cooling=cooling;
    return out;
  }
};
