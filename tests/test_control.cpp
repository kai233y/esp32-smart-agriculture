#include "../firmware/controller/include/control.h"
#include <assert.h>
#include <stdio.h>

int main() {
  Policy p; p.autoEnabled=true;
  Override m[3]; Inputs s; s.soil=20; s.temperature=32; s.lux=100; s.tankOk=true;
  Controller c;
  assert(!c.step(0,s,p,m).pump); // boot cooldown
  auto o=c.step(300000,s,p,m); assert(o.pump && o.fan && o.light);
  assert(c.step(314900,s,p,m).pump);
  assert(!c.step(315000,s,p,m).pump); // exact maximum duration
  assert(!c.step(614900,s,p,m).pump);
  assert(c.step(615000,s,p,m).pump);
  s.tankOk=false; assert(!c.step(615100,s,p,m).pump);
  m[0].mode=ON; m[0].until=1000000;
  assert(!c.step(915100,s,p,m).pump); // manual cannot bypass low tank
  s.tankOk=true; s.soil=NAN; assert(!c.step(915200,s,p,m).pump);
  s.soil=90; assert(c.step(915300,s,p,m).pump); // explicit manual on
  m[0].until=915400; assert(!c.step(915400,s,p,m).pump); // TTL expires
  m[0]=Override();
  s.temperature=NAN; assert(c.step(915500,s,p,m).fan); // automatic failsafe ventilation
  p.autoEnabled=false; assert(!c.step(915600,s,p,m).fan);
  Controller hysteresis; p.autoEnabled=true; s.soil=20; s.temperature=32;
  assert(hysteresis.step(400000,s,p,m).pump);
  s.soil=35; s.temperature=29; s.lux=3000;
  o=hysteresis.step(400100,s,p,m); assert(o.pump && o.fan && o.light);
  s.soil=46; s.temperature=26; s.lux=5000;
  o=hysteresis.step(400200,s,p,m); assert(!o.pump && !o.fan && !o.light);
  Controller wrap; s.soil=20;
  uint32_t start=UINT32_MAX-5000;
  assert(wrap.step(start,s,p,m).pump);
  assert(wrap.step(9000,s,p,m).pump);
  assert(!wrap.step(10000,s,p,m).pump); // millis() wraparound
  Override ttl; ttl.mode=ON; ttl.until=100;
  assert(ttl.active(UINT32_MAX-50)==ON && ttl.active(100)==AUTO);
  assert(ttl.active(0x80000100U)==AUTO); // expired override stays expired for weeks
  p.soilOff=10; assert(!validPolicy(p));
  puts("control safety checks passed");
}
