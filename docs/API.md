# HTTP接口

基础URL示例：`http://192.168.1.100:8000`。除首页、静态资源、健康检查和OpenAPI文档外，接口均要求请求头 `X-API-Key`。

| 角色 | 配置变量 | 权限 |
|---|---|---|
| 管理员 | ADMIN_TOKEN | 控制台、策略、手动指令、上传及查看图片 |
| 控制器 | DEVICE_TOKEN | 仅上传遥测、读取控制策略 |
| 摄像头 | CAMERA_TOKEN | 仅上传图片 |

三个令牌不同且至少24字符，服务启动强制检查。该鉴权面向可信局域网原型，没有注册/找回/多用户系统。浏览器使用内存中的令牌，页面刷新需重新输入。

## 设备上报

`POST /api/telemetry`，DEVICE_TOKEN，JSON：

```json
{
  "device_id":"farm-01", "uptime_s":120,
  "temperature":26.5,"humidity":65.0,"soil_pct":32.5,
  "soil_raw":2450,"lux":3200.0,"tank_ok":true,
  "pump":false,"fan":false,"light":false,"pump_cooling":true,
  "auto_enabled":false,"policy_revision":1,"faults":[]
}
```

传感器故障以JSON `null` 上报，不可传NaN/Infinity。输出状态来自控制器实际GPIO目标状态，**不是继电器触点或流量的实测反馈**。时间戳由服务器接收时生成，固件不需要NTP。约35秒没收到遥测则控制台判定离线。

## 策略下发

`GET /api/control`，DEVICE_TOKEN，返回：

```json
{
  "revision":1,
  "policy":{
    "auto_enabled":false,"soil_on":30,"soil_off":45,
    "temp_on":30,"temp_off":27,"lux_on":2000,"lux_off":4000,
    "pump_max_s":15,"pump_cooldown_s":300
  },
  "manual":{"fan":{"mode":"on","remaining_s":45}}
}
```

ESP32约3秒轮询，保存变化后的策略到NVS。每个手动覆盖按服务器剩余时间转换为本地millis截止时间，断网也会到期，重启不恢复旧手动状态。服务器时钟应保持稳定，避免手动TTL受墙钟大幅跳变影响。水泵保护由本地独立控制任务执行。

`PUT /api/policy`，ADMIN_TOKEN，提交完整的policy对象（同上）。检查土壤/温度/光照回差和限时范围，成功后revision加一。网页用遥测的policy_revision确认设备应用情况。

## 手动控制

`POST /api/commands`，ADMIN_TOKEN：

```json
{"target":"pump","mode":"on","ttl_s":15}
```

- target：pump / fan / light。
- mode：on / off / auto。auto表示删除该执行器的临时覆盖，恢复本地自动策略。
- ttl_s：1–300秒。默认网页水泵15秒、风扇和灯60秒。固件单次最长浇水上限仍优先，且初始冷却期内不会开泵。
- HTTP成功只代表服务器接收，等待设备轮询和遥测确认。自动关闭时，auto表示回到关闭的自动策略。OFF覆盖到期后可能由自动策略重新开启。

## 图片上传与读取

`POST /api/images`，CAMERA_TOKEN或ADMIN_TOKEN，**直接发送JPEG/PNG二进制请求体，不是multipart**。

```bash
curl -X POST http://服务器IP:8000/api/images \
  -H "X-API-Key: 摄像头令牌" -H "Content-Type: image/jpeg" \
  --data-binary @leaf.jpg
```

上限2MiB、800万像素，服务重编码JPEG，最长边≤1600。返回示例（未配置模型）：

```json
{"id":"32位十六进制ID","status":"model_unavailable","detections":[],
 "message":"尚未加载农业专用模型，图片已保存，未作出病虫害判断","width":800,"height":600}
```

有模型时每个检测项为 `class_id / label / confidence / xyxy`。xyxy为保存图片中的像素坐标。状态区分detected、no_detection、model_unavailable、inference_error。加载失败时 `/health` 的model为error，仍能采图和使用环境控制功能。

`GET /api/images/{id}` 仅管理员可用，返回JPEG，不能把令牌放在URL参数中。网页通过带鉴权的fetch拿到Blob后显示。

## 管理与运维

- `GET /api/dashboard`：ADMIN_TOKEN，返回最近遥测、趋势、期望策略、最近12张图、最近20条操作。
- `GET /health`：无需令牌，返回服务状态和model状态；不代表设备在线或传感器正常。
- `GET /docs`：自动生成接口文档，调用受保护接口仍需添加X-API-Key请求头。
- 数据保留：10000次遥测、200张图片；操作审计不自动删除。SQLite备份应使用在线备份API或停服后复制完整数据库（包含WAL时不要只复制主文件）。

当前为单进程、单区实现。增加worker、多相机并发或多设备前，应替换单进程图片锁、扩展队列与设备级鉴权/存储。
