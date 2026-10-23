'use strict';
const $=id=>document.getElementById(id);
let token='', busy=false, state=null, policyLoaded=false, selectedImage='', imageBusy=false;
const labels={pump:'灌溉水泵',fan:'通风风扇',light:'补光灯'};
const fields=[['soil_on','开始浇水 · 土壤 %',0,95,1],['soil_off','停止浇水 · 土壤 %',5,100,1],['temp_on','启动风扇 · ℃',5,60,.5],['temp_off','停止风扇 · ℃',0,55,.5],['lux_on','启动补光 · lx',0,100000,1],['lux_off','停止补光 · lx',1,150000,1],['pump_max_s','单次最长浇水 · 秒',1,60,1],['pump_cooldown_s','停泵冷却间隔 · 秒',30,3600,1]];
for(const [id,label,min,max,step] of fields){const l=document.createElement('label');l.textContent=label;const i=document.createElement('input');Object.assign(i,{id,type:'number',min,max,step,required:true});l.append(i);$('policyFields').append(l);}
for(const [target,label] of Object.entries(labels)){
  const row=document.createElement('div');row.className='actuator';
  const name=document.createElement('div');name.textContent=label;
  const status=document.createElement('span');status.id=target+'State';status.className='state';name.append(status);row.append(name);
  const actions=document.createElement('div');actions.className='actions';
  for(const [mode,text] of [['on','开启'],['off','关闭'],['auto','恢复自动']]){const b=document.createElement('button');b.textContent=text;b.onclick=()=>sendCommand(target,mode);actions.append(b);}
  row.append(actions);$('actuators').append(row);
}
function notice(message){$('notice').textContent=message;}
async function api(path,options={}){
  const response=await fetch('/api/'+path,{...options,headers:{'X-API-Key':token,...options.headers},signal:AbortSignal.timeout(45000)});
  if(!response.ok){let detail;try{detail=JSON.stringify((await response.json()).detail);}catch{detail='请求失败';}throw new Error('HTTP '+response.status+' '+detail);}
  return response;
}
const fmt=(v,unit)=>v===null||v===undefined?'—':Number(v).toFixed(1)+unit;
const clock=t=>new Date(t*1000).toLocaleString('zh-CN',{hour12:false});
function render(s){
  state=s;const t=s.latest;
  $('connection').textContent=s.online?'设备在线':'设备离线 / 尚无遥测';$('connection').className='badge'+(s.online?' good':'');
  $('updated').textContent=t?'最后采样 '+clock(t.ts):'等待设备上报';
  $('metrics').replaceChildren();
  for(const [title,value,hint] of [['空气温度',fmt(t?.temperature,'℃'),'DHT22'],['空气湿度',fmt(t?.humidity,'%'),'相对湿度'],['土壤湿度',fmt(t?.soil_pct,'%'),'两点相对标定'],['环境光照',fmt(t?.lux,' lx'),'BH1750'],['水箱水位',t?(t.tank_ok?'水量充足':'缺水 / 断线'):'—','浮球安全联锁']]){
    const card=document.createElement('div');card.className='metric';
    const a=document.createElement('label');a.textContent=title;const v=document.createElement('div');v.className='value';v.textContent=value;const h=document.createElement('small');h.textContent=hint;card.append(a,v,h);$('metrics').append(card);
  }
  $('faults').textContent=!t?'暂无遥测':(t.faults.length?'设备告警：'+t.faults.join('、'):(s.online?'传感器状态正常':'设备离线，当前显示最后收到的数据'));
  $('faults').className='warning'+(t&&!t.faults.length&&s.online?' ok':'');
  for(const name of Object.keys(labels)) $(name+'State').textContent=t?(t[name]?'运行中':'已关闭'):'未知';
  $('policyStatus').textContent=`服务器策略 v${s.revision} / 设备回报 ${t?'v'+t.policy_revision:'未知'}；设备自动控制${t?(t.auto_enabled?'已启用':'已关闭'):'未知'}${t?.pump_cooling?'；水泵冷却中':''}。`;
  if(!policyLoaded){for(const [id] of fields) $(id).value=s.policy[id];$('auto_enabled').checked=s.policy.auto_enabled;policyLoaded=true;}
  $('model').textContent={ready:'农业模型已加载',unavailable:'未配置模型',error:'模型加载失败'}[s.model]||s.model;
  drawTrend(s.history);
  $('imageList').replaceChildren();
  for(const im of s.images){const b=document.createElement('button');b.textContent=clock(im.ts);b.onclick=()=>showImage(im);$('imageList').append(b);}
  if(s.images.length&&!selectedImage) showImage(s.images[0]);
  $('audit').replaceChildren();
  for(const a of s.audit){const e=document.createElement('div');e.textContent=clock(a.ts)+' · '+a.action+' · '+a.body;$('audit').append(e);}
}
function drawTrend(rows){
  const svg=$('trend'),ns='http://www.w3.org/2000/svg';svg.replaceChildren();
  for(const n of [0,25,50,75,100]){const y=165-n*1.4;const l=document.createElementNS(ns,'line');for(const [k,v] of Object.entries({x1:36,x2:688,y1:y,y2:y,stroke:'#dde7da'}))l.setAttribute(k,v);svg.append(l);const t=document.createElementNS(ns,'text');t.setAttribute('x',3);t.setAttribute('y',y+4);t.setAttribute('font-size',11);t.setAttribute('fill','#71846e');t.textContent=n;svg.append(t);}
  for(const [key,color] of [['soil_pct','#3e8247'],['humidity','#4e8cad']]){
    let d='',open=false;
    rows.forEach((r,i)=>{if(r[key]===null){open=false;return;}d+=(open?' L':' M')+(36+i*652/Math.max(1,rows.length-1))+' '+(165-r[key]*1.4);open=true;});
    const p=document.createElementNS(ns,'path');p.setAttribute('d',d);p.setAttribute('fill','none');p.setAttribute('stroke',color);p.setAttribute('stroke-width',2.5);svg.append(p);
  }
}
async function refresh(){if(!token||busy||document.hidden)return;busy=true;try{render(await (await api('dashboard')).json());if($('notice').textContent==='正在连接…')notice('已连接管理服务，请核对设备状态。');}catch(e){$('connection').textContent='服务连接失败';notice(e.message);}finally{busy=false;}}
async function sendCommand(target,mode){if(!token)return notice('请先输入管理员令牌并连接');try{const ttl_s=target==='pump'?15:60;await api('commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target,mode,ttl_s})});notice(`${labels[target]}指令已保存${mode==='auto'?'':'，有效期 '+ttl_s+' 秒'}；等待设备回报执行状态。`);await refresh();}catch(e){notice(e.message);}}
$('connect').onclick=()=>{token=$('token').value.trim();policyLoaded=false;selectedImage='';notice('正在连接…');refresh();};
$('policyForm').onsubmit=async e=>{e.preventDefault();if(!token)return notice('请先连接');const p={auto_enabled:$('auto_enabled').checked};for(const [id] of fields)p[id]=Number($(id).value);try{await api('policy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});notice('策略已保存，等待设备轮询应用；请核对设备回报版本。');policyLoaded=false;await refresh();}catch(err){notice(err.message);}};
async function showImage(im){
  if(imageBusy)return;imageBusy=true;
  try{const blob=await (await api('images/'+im.id)).blob();const url=URL.createObjectURL(blob);try{const img=new Image();img.src=url;await img.decode();const c=$('photo');c.width=img.width;c.height=img.height;c.style.display='block';const ctx=c.getContext('2d');ctx.drawImage(img,0,0);ctx.lineWidth=Math.max(2,img.width/300);ctx.font=`${Math.max(14,img.width/45)}px sans-serif`;
    for(const d of im.detections){const [x1,y1,x2,y2]=d.xyxy;ctx.strokeStyle='#e7fa68';ctx.fillStyle='#e7fa68';ctx.strokeRect(x1,y1,x2-x1,y2-y1);ctx.fillText(d.label+' '+Math.round(d.confidence*100)+'%',Math.max(0,x1),Math.max(20,y1-5));}
    const status={model_unavailable:'尚未配置模型，未进行识别',no_detection:'未检出目标（不代表健康）',inference_error:'推理失败，请检查服务日志',detected:'检出 '+im.detections.map(d=>d.label+' '+Math.round(d.confidence*100)+'%').join('、')};$('recognition').textContent=clock(im.ts||Date.now()/1000)+' · '+(status[im.status]||im.status);selectedImage=im.id;
  }finally{URL.revokeObjectURL(url);}}catch(e){notice(e.message);}finally{imageBusy=false;}
}
$('imageFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;if(!token){notice('请先连接');return;}if(file.size>2097152){notice('图片不能超过2 MiB');return;}e.target.disabled=true;$('recognition').textContent='正在上传和分析…';try{const im=await(await api('images',{method:'POST',headers:{'Content-Type':file.type},body:file})).json();await showImage(im);await refresh();}catch(err){notice(err.message);}finally{e.target.disabled=false;e.target.value='';}};
setInterval(refresh,5000);render({latest:null,online:false,revision:1,policy:{},history:[],images:[],model:'unavailable',audit:[]});policyLoaded=false;
