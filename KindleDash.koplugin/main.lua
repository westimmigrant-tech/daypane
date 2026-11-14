-- DayPane · Kindle 端显示插件（单一实现 v0.3.0）
--
-- 把渲染好的整屏 PNG（1072x1448，1-bit 抖动 / ~30KB）满屏显示。
-- 看板图片在服务端生成，Kindle 在本机叠加电量。
--
-- 取图三级降级链：
--   1) 局域网 PC（/api/display 清单 + /api/image）—— 数据最新，电脑关机时连不上
--   2) 云端静态图（GitHub Pages manifest）—— GitHub Actions 每半小时渲染，电脑关机仍可用
--   3) 本地持久缓存（settings 目录）—— 网络全断时显示最后一次的图
--
-- 自动刷新：看板显示期间默认保持唤醒，UI 定时器在请求完成后安排下一次。
-- 手动休眠暂停 UI 更新，唤醒后恢复。周期 RTC 默认关闭，仍属机型相关实验。
-- 不在唤醒边界同步调用 resetT1Timeout。
--
-- 历史坑：本插件曾拆成 main.lua + runtime.lua 两层，runtime 由 dofile 在 main 之后执行、
-- 静默覆盖 main 的同名函数，导致 main 里大量代码是死代码、改一处不生效。v0.3.0 合并为
-- 单一实现，杜绝覆盖。

local WidgetContainer = require("ui/widget/container/widgetcontainer")
local UIManager = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local InputContainer = require("ui/widget/container/inputcontainer")
local ImageWidget = require("ui/widget/imagewidget")
local TextWidget = require("ui/widget/textwidget")
local Font = require("ui/font")
local Blitbuffer = require("ffi/blitbuffer")
local ffiutil = require("ffi/util")
local time = require("ui/time")
local Geom = require("ui/geometry")
local Device = require("device")
local Screen = Device.screen
local ltn12 = require("ltn12")
local GestureRange = require("ui/gesturerange")
local http = require("socket.http")
local LuaSettings = require("luasettings")
local DataStorage = require("datastorage")
local RenderImage = require("ui/renderimage")
local lfs = require("libs/libkoreader-lfs")
local logger = require("logger")
local JSON = require("json")

local VERSION = "0.3.3"
local REFRESH_SEC = 30 * 60
local MAX_IMAGE = 4 * 1024 * 1024
local DEFAULT_HOST = ""
local DEFAULT_PORT = "8787"
-- 云端静态图完整 URL（GitHub Pages），留空则只用局域网
local DEFAULT_CLOUD = "https://shenliucn-prog.github.io/daypane/screen.png"
local CACHE_IMG_NAME = "kindledash_screen.png"
local CACHE_TS_NAME  = "kindledash_ts.txt"

-- KOReader 的 CA 证书实际在 <datadir>/data/ca-bundle.crt（少写 /data 一层就会一直
-- 报 "CA bundle missing"，云端 https 永远取不到图）。运行时探测两个候选路径。
local function caBundlePath(exists)
    local base = DataStorage:getDataDir()
    local primary = base .. "/data/ca-bundle.crt"
    if exists(primary) then return primary end
    local fallback = base .. "/ca-bundle.crt"
    if exists(fallback) then return fallback end
    return nil
end

-- SHA-256（同目录 sha256.lua，纯 LuaJIT 实现）。加载失败时降级为不做校验，
-- 绝不因此让整个插件加载失败。
local sha256
do
    local ok, mod = pcall(function()
        local src = debug.getinfo(1, "S").source
        local dir = src and src:match("^@(.*)/")
        if not dir then return nil end
        return dofile(dir .. "/sha256.lua")
    end)
    if ok and type(mod) == "function" then sha256 = mod end
end

local KindleDash = WidgetContainer:new{
    name = "KindleDash",
    is_doc_only = false,
    sorting_hint = "tools",
}

-- 暴露给实例，便于显式替换（例如测试注入已知实现）。nil = 不做校验。
KindleDash.sha256 = sha256

-- ---------------- 文案 / 语言 ----------------

-- Interface locale is independent of the Kindle firmware language.
-- Preserve Chinese for existing plugin settings; new installs follow KOReader.
function KindleDash:loadLanguage()
    local settings = LuaSettings:open(self:settingsPath())
    local saved = settings:readSetting("language")
    if saved == "zh" or saved == "en" then return saved end
    if settings:has("host") or settings:has("cloud") or settings:has("port") then
        settings:saveSetting("language", "zh")
        settings:flush()
        return "zh"
    end
    local locale = G_reader_settings and G_reader_settings:readSetting("language") or "en"
    local language = tostring(locale):lower():match("^zh") and "zh" or "en"
    settings:saveSetting("language", language)
    settings:flush()
    return language
end

local EN = {
    ["本机"] = "LAN",
    ["云端"] = "Cloud",
    ["未配置任何图源"] = "No image source configured",
    [" 均不可用"] = " unavailable",
    ["看板显示失败:\n"] = "Unable to display dashboard:\n",
    ["看板失败:\n"] = "Dashboard error:\n",
    ["离线 · 最后 "] = "Offline · Last image: ",
    ["离线 · 显示上次缓存"] = "Offline · Showing cached image",
    ["刷新失败: "] = "Refresh failed: ",
    ["来自云端（电脑未连上）"] = "Cloud image (LAN unavailable)",
    ["自动刷新: 开 (整点/半点)"] = "Auto refresh: on (:00 / :30)",
    ["自动刷新: 关"] = "Auto refresh: off",
    ["服务器地址 (IP:端口)"] = "LAN server (IP:port)",
    ["例如 192.168.31.188:8787"] = "Example: 192.168.1.23:8787",
    ["取消"] = "Cancel",
    ["保存"] = "Save",
    ["已保存: "] = "Saved: ",
    ["云端图地址 (完整 URL)"] = "Cloud image URL (full URL)",
    ["电脑关机时从这儿取图。留空则只用局域网。"] = "Used when the computer is unavailable. Leave empty for LAN only.",
    ["https://用户名.github.io/daypane/screen.png"] = "https://username.github.io/daypane/screen-en.png",
    ["已清空云端地址"] = "Cloud URL cleared",
    ["刷新看板"] = "Refresh dashboard",
    ["设置局域网服务器"] = "Set LAN server",
    ["设置云端图地址"] = "Set cloud image URL",
    ["切换自动刷新 (整点/半点)"] = "Toggle auto refresh (:00 / :30)",
    ["设备状态"] = "Device status",
    ["电量"] = "Battery",
    ["刷新间隔"] = "Refresh interval",
    ["刷新间隔（分钟，5–1440）"] = "Refresh interval (minutes, 5–1440)",
    ["设置：图片或清单地址"] = "Setup: image or manifest URL",
    ["使用自己的图源或内置示例。城市、时区和布局在生成端配置。保存后测试图片。"] = "Use your own server or the built-in demo. City, timezone and layout are configured on the renderer. Then choose Test image.",
    ["保存并测试"] = "Save & test",
    ["实验：单次 RTC 唤醒测试"] = "Experimental: one RTC wake test",
    ["此设备不支持 RTC 唤醒接口。"] = "RTC wake is unavailable on this device.",
    ["实验：休眠一次并尝试在2分钟后唤醒。请确保可按电源键恢复。不会开启循环休眠。"] = "Experimental: sleep once and attempt a wake in 2 minutes. Keep the power button accessible. This does not enable recurring sleep.",
    ["关于"] = "About",
    ["每半小时 RTC 唤醒刷新，设备平时正常休眠\n"] = "RTC wake every half hour; the device sleeps normally in between\n",
    ["常亮看板模式（看板显示时防休眠）"] = "Stay awake while dashboard is shown",
    ["常亮: 开（看板显示期间设备不休眠）"] = "Hold awake: on (no sleep while the dashboard is shown)",
    ["常亮: 关"] = "Hold awake: off",
    ["看板显示期间保持常亮并定时刷新\n"] = "Stays awake while the dashboard is shown and refreshes on schedule\n",
    ["开"] = "ON",
    ["关"] = "OFF",
    ["AI 额度走局域网实时，关机显示最后值\n"] = "AI activity estimates may retain old values offline\n",
    ["顶部下滑/顶部点击返回看板退出"] = "Tap / swipe down from the top to exit",
}
function KindleDash:tr(text)
    return self.language == "en" and (EN[text] or text) or text
end

function KindleDash:setLanguage(language)
    local settings = LuaSettings:open(self:settingsPath())
    settings:saveSetting("language", language)
    self.language = language
    -- Only migrate our built-in URLs; custom image endpoints remain user-owned.
    if self.cloud == DEFAULT_CLOUD or self.cloud == DEFAULT_CLOUD:gsub("screen.png$", "screen-en.png") then
        self.cloud = language == "en" and DEFAULT_CLOUD:gsub("screen.png$", "screen-en.png") or DEFAULT_CLOUD
        settings:saveSetting("cloud", self.cloud)
    end
    settings:flush()
    UIManager:show(InfoMessage:new{
        text = language == "en" and "English selected. Reopen the menu to refresh labels." or "已选择中文，重新打开菜单即可更新。",
        timeout = 4,
    })
end

-- ---------------- 设置读写 ----------------

function KindleDash:settingsPath()
    return DataStorage:getSettingsDir() .. "/kindledash.lua"
end
function KindleDash:cacheDir()
    return DataStorage:getSettingsDir()
end
function KindleDash:cacheImg()
    return self:cacheDir() .. "/" .. (self.language == "en" and "kindledash_screen_en.png" or CACHE_IMG_NAME)
end
function KindleDash:cacheTs()
    return self:cacheDir() .. "/" .. (self.language == "en" and "kindledash_ts_en.txt" or CACHE_TS_NAME)
end
function KindleDash:ensureCacheDir()
    local dir = self:cacheDir()
    if dir and lfs.attributes(dir, "mode") ~= "directory" then
        lfs.mkdir(dir)
    end
end
function KindleDash:readTs()
    local f = io.open(self:cacheTs(), "rb")
    if not f then return nil end
    local s = f:read("*a"); f:close()
    return s and s:match("^%s*(.-)%s*$") or nil
end
function KindleDash:writeTs(s)
    self:ensureCacheDir()
    local f = io.open(self:cacheTs(), "wb")
    if not f then return nil end
    f:write(s or ""); f:close()
    return true
end
function KindleDash:fileExists(path)
    local f = io.open(path, "rb")
    if f then f:close(); return true end
    return false
end

-- 设置项统一走 LuaSettings，任何异常都降级为默认值，避免设置缺失时崩。
function KindleDash:preferences()
    return LuaSettings:open(self:settingsPath())
end
function KindleDash:option(key, default)
    local ok, v = pcall(function() return self:preferences():readSetting(key) end)
    if not ok or v == nil then return default end
    return v
end
function KindleDash:setOption(key, value)
    pcall(function()
        local s = self:preferences(); s:saveSetting(key, value); s:flush()
    end)
end
function KindleDash:label(en, zh)
    return self.language == "en" and en or zh
end

function KindleDash:loadHost()
    local host, port = DEFAULT_HOST, DEFAULT_PORT
    local ok, s = pcall(function() return LuaSettings:open(self:settingsPath()) end)
    if ok and s then
        if s:has("host") then host = s:readSetting("host") or DEFAULT_HOST end
        if s:has("port") then port = s:readSetting("port") or DEFAULT_PORT end
    end
    if host and host ~= "" and not host:find(":", 1, true) then
        host = host .. ":" .. port
    end
    return host
end
function KindleDash:saveHost(host)
    host = tostring(host or ""):match("^%s*(.-)%s*$") or ""
    local ok, s = pcall(function() return LuaSettings:open(self:settingsPath()) end)
    if ok and s then
        if host == "" then
            s:saveSetting("host", "")
        else
            local h, p = host:match("^(.-):(%d+)$")
            if h and p then
                s:saveSetting("host", h)
                s:saveSetting("port", p)
            else
                -- 用户只填了 IP：补上当前端口，否则本次会话会去连 80 端口，
                -- 一直失败到重启后 loadHost 才修正。
                s:saveSetting("host", host)
                host = host .. ":" .. (s:readSetting("port") or DEFAULT_PORT)
            end
        end
        s:flush()
    end
    self.host = host
end
function KindleDash:loadCloud()
    local ok, s = pcall(function() return LuaSettings:open(self:settingsPath()) end)
    if ok and s and s:has("cloud") then
        local cloud = s:readSetting("cloud") or DEFAULT_CLOUD
        local legacy = "https://shenliucn-prog.github.io/shawn-kanban/"
        if cloud == legacy .. "screen.png" then
            cloud = DEFAULT_CLOUD
        elseif cloud == legacy .. "screen-en.png" then
            cloud = DEFAULT_CLOUD:gsub("screen.png$", "screen-en.png")
        end
        if cloud ~= s:readSetting("cloud") then
            s:saveSetting("cloud", cloud)
            s:flush()
        end
        return cloud
    end
    return self.language == "en" and DEFAULT_CLOUD:gsub("screen.png$", "screen-en.png") or DEFAULT_CLOUD
end
function KindleDash:saveCloud(url)
    local ok, s = pcall(function() return LuaSettings:open(self:settingsPath()) end)
    if ok and s then
        s:saveSetting("cloud", url or "")
        s:flush()
    end
    self.cloud = url or ""
end

-- ---------------- 健康日志（诊断用，写到缓存目录） ----------------

function KindleDash:batteryCapacity()
    local ok, capacity = pcall(function() return Device:getPowerDevice():getCapacity() end)
    if ok and type(capacity) == "number" and capacity >= 0 and capacity <= 100 then
        return math.floor(capacity + 0.5)
    end
end

function KindleDash:record(event, detail)
    local ok, err = pcall(function()
        self:ensureCacheDir()
        local dir = self:cacheDir()
        if not dir then return end
        local path = dir .. "/kindledash-health.json"
        local history = self._health_history or {}
        history[#history + 1] = {
            at = os.time(), event = event,
            detail = tostring(detail or ""):sub(1, 240),
            battery = self:batteryCapacity(),
        }
        while #history > 192 do table.remove(history, 1) end
        self._health_history = history
        local f = io.open(path .. ".tmp", "wb")
        if f then f:write(JSON.encode(history)); f:close(); os.rename(path .. ".tmp", path) end
    end)
    if not ok then logger.warn("ShawnKanban record failed", tostring(err)) end
end

-- ---------------- 电源与刷新调度 ----------------
-- 设备允许正常休眠；用 RTC 定时唤醒实现省电的"半小时自动刷新"。
-- 设备醒着时另有 UI 定时器（armAutoRefresh）兜底。

function KindleDash:retryDelay()
    return math.min(1800, 60 * 2 ^ math.min((self._failures or 1) - 1, 5))
end

-- 刷新间隔（秒）。所有调度入口都必须走这里，否则不同函数钳制不一致，
-- 会出现"定时器按 5 分钟算、RTC 按 60 秒算"的错位。
function KindleDash:intervalSec()
    local interval = tonumber(self:option("interval", REFRESH_SEC)) or REFRESH_SEC
    return math.max(300, math.min(86400, interval))
end

-- 到下一个整点/半点的秒数（间隔可配置，默认 30 分钟）。
function KindleDash:nextDelay()
    local interval = self:intervalSec()
    if self:option("night_mode", false) then
        local hour = os.date("*t").hour
        if hour >= 23 or hour < 7 then interval = math.max(interval, 7200) end
    end
    return interval - os.time() % interval
end

-- 把任务推迟到 UI 事件循环的下一拍执行（KOReader 的 nextTick 就是 scheduleIn(0)）。
function KindleDash:defer(fn)
    self:cancelDeferred()
    self._deferred = fn
    if UIManager.nextTick then
        UIManager:nextTick(fn)
    else
        UIManager:scheduleIn(0, fn)
    end
    return fn
end

function KindleDash:cancelDeferred()
    if self._deferred then
        UIManager:unschedule(self._deferred)
        self._deferred = nil
    end
end

-- 注册一次性 RTC 唤醒任务：到点唤醒设备 → 刷新 → 再注册下一次。
-- KOReader 在 ReadyToSuspend 时才通过 lipc 把队首任务写进 powerd 的 RTC 闹钟，
-- 所以任务必须"常备"：任何时刻队里都得有一条。
function KindleDash:scheduleRtcWake()
    if not self.auto_on then
        self:cancelRtcWake()
        return
    end
    -- PW3 实测：闹钟能唤醒硬件，但滞后 ~90-100s，被 wakeupAction(90) 拒收，
    -- 任务永远不执行。默认关闭 RTC 唤醒（常亮模式取而代之）；
    -- 想在别的机型上试，可在设置里开 rtc_wake。
    if not self:option("rtc_wake", false) then return end
    local mgr = Device.wakeup_mgr
    if not mgr then
        -- Kindle 上 wakeup_mgr 由 KindlePowerD:initWakeupMgr() 创建，需要 lipc 可用
        -- 且设备支持屏保。没有它就退化成"只有醒着才刷新"——必须留下证据，
        -- 否则"不自动刷新"会再次变成无迹可循的静默故障。
        if not self._rtc_warned then
            self._rtc_warned = true
            logger.warn("ShawnKanban: no RTC wakeup manager; auto-refresh only runs while awake")
            self:record("rtc_unavailable")
        end
        return
    end
    if Device.canSuspend and not Device:canSuspend() then return end
    local interval = self:intervalSec()
    local delay = self:nextDelay()
    if delay < 30 then delay = delay + interval end
    if self._rtc_task then
        pcall(function() mgr:removeTasks(nil, self._rtc_task) end)
        self._rtc_task = nil
    end
    self._rtc_task = function()
        self._suspended = false
        self:record("rtc_wake_fired")
        -- ⚠️ WakeupMgr:wakeupAction() 会在本回调返回后立刻 removeTask(1)。
        -- 如果在这里同步重排任务，新任务会排到队首、被那次 removeTask 一并删掉，
        -- 唤醒链当场断掉（之后每次唤醒都变成"no tasks"而静默失效）。
        -- 因此重排与刷新都必须推迟到下一拍。
        self:defer(function()
            self._deferred = nil
            self:scheduleRtcWake()
            pcall(function() self:requestRefresh(true, false) end)
        end)
    end
    local ok = pcall(function() mgr:addTask(delay, self._rtc_task) end)
    if ok then self._next_attempt = os.time() + delay end
end

function KindleDash:cancelRtcWake()
    self:cancelDeferred()
    if self._rtc_task and Device.wakeup_mgr then
        local task = self._rtc_task
        self._rtc_task = nil
        pcall(function() Device.wakeup_mgr:removeTasks(nil, task) end)
    end
end

-- UI 定时器：设备醒着时按整点/半点刷新；同时维持 RTC 任务链。
-- 自动刷新只在看板显示时运行——看板关掉后没必要继续每半小时唤醒设备。
function KindleDash:armAutoRefresh(delay)
    if self._auto_timer then UIManager:unschedule(self._auto_timer) end
    self:cancelRtcWake()
    self._next_attempt = nil
    if not self.auto_on or not self.dash_widget then return end
    local function tick()
        if not self.auto_on then return end
        if self.dash_widget and not self._suspended then
            pcall(function() self:requestRefresh(true, false) end)
        end

    end
    self._auto_timer = tick
    local first = delay or self:nextDelay()
    self._next_attempt = os.time() + first
    UIManager:scheduleIn(first, tick)
    self:scheduleRtcWake()
end

function KindleDash:toggleAutoRefresh()
    self.auto_on = not self.auto_on
    self:setOption("auto_refresh", self.auto_on)
    if self._auto_timer then UIManager:unschedule(self._auto_timer) end
    if self.auto_on then
        self:armAutoRefresh()
        UIManager:show(InfoMessage:new{ text = self:tr("自动刷新: 开 (整点/半点)"), timeout = 2 })
    else
        self:cancelRtcWake()
        UIManager:show(InfoMessage:new{ text = self:tr("自动刷新: 关"), timeout = 2 })
    end
end

-- ---------------- 取图 ----------------

function KindleDash:endpoints()
    local list = {}
    if self.host and self.host ~= "" then
        table.insert(list, { url = "http://" .. self.host .. "/api/screen?lang=" .. (self.language or "zh"), name = self:tr("本机") })
    end
    if self.cloud and self.cloud ~= "" then
        table.insert(list, { url = self.cloud, name = self:tr("云端") })
    end
    return list
end

-- 由图片 URL 推导清单（manifest）URL；返回 nil 表示该端点不支持清单协议。
function KindleDash:manifestUrl(image)
    if image:match("%.json$") then return image end
    if image:match("/api/screen") then return image:gsub("/api/screen", "/api/display") end
    if image:match("^https://shenliucn%-prog%.github%.io/daypane/") then
        if image:match("/screen%-en%.png$") then return image:gsub("screen%-en%.png$", "en/manifest.json") end
        return image:gsub("screen.png$", "manifest.json")
    end
end

local function resolve(base, value)
    if type(value) ~= "string" then return nil end
    if value:match("^https?://") then
        if base:match("^https://") and not value:match("^https://") then return nil end
        return value
    end
    if value:sub(1, 2) == "//" or value:find("..", 1, true) then return nil end
    if value:sub(1, 1) == "/" then return base:match("^(https?://[^/]+)") .. value end
    return base:match("^(.*)/") .. "/" .. value
end

-- 通用 HTTP GET（带大小上限 / 超时 / https CA 校验），返回 body 或 nil, err。
function KindleDash:request(url, limit)
    if not url:match("^https?://") then return nil, "Unsupported URL" end
    local chunks, bytes = {}, 0
    local req = {
        url = url, method = "GET", redirect = false,
        headers = { ["User-Agent"] = "ShawnKanban/" .. VERSION },
        sink = function(chunk)
            if chunk then
                bytes = bytes + #chunk
                if bytes > limit then return nil, "Response exceeds size limit" end
                chunks[#chunks + 1] = chunk
            end
            return 1
        end,
    }
    if url:match("^https://") then
        local ca = caBundlePath(function(p) return self:fileExists(p) end)
        if not ca then return nil, "CA bundle missing" end
        req.cafile, req.verify, req.protocol = ca, "peer", "tlsv1_2"
    end
    local https = url:match("^https://") and require("ssl.https")
    local old_http, old_https = http.TIMEOUT, https and https.TIMEOUT
    http.TIMEOUT = 8
    if https then https.TIMEOUT = 8 end
    local ok, result, status = pcall(http.request, req)
    http.TIMEOUT = old_http
    if https then https.TIMEOUT = old_https end
    if not ok or not result or tonumber(status) ~= 200 then
        return nil, "HTTP/network: " .. tostring(status or result)
    end
    if bytes > limit then return nil, "Response exceeds size limit" end
    return table.concat(chunks)
end

function KindleDash:readMetadata()
    local f = io.open(self:cacheImg() .. ".json", "rb")
    if not f then return {} end
    local text = f:read("*a"); f:close()
    local ok, value = pcall(JSON.decode, text)
    return ok and type(value) == "table" and value or {}
end

-- 三级降级取图；返回 body, err, sourceName, unchanged。
function KindleDash:fetchScreen()
    local sha = self.sha256
    local failures = {}
    for _, ep in ipairs(self:endpoints()) do
        local manifest_url = self:manifestUrl(ep.url)
        local meta, url = {}, ep.url
        local error_message
        if manifest_url then
            local raw, err = self:request(manifest_url, 128 * 1024)
            local ok, value = pcall(JSON.decode, raw or "")
            if not ok or type(value) ~= "table" or value.schemaVersion ~= 1
                or type(value.sha256) ~= "string" or not value.sha256:match("^%x+$") or #value.sha256 ~= 64
                or type(value.generatedAt) ~= "number" or value.generatedAt <= 0
                or value.generatedAt > os.time() * 1000 + 300000 then
                error_message = err or "Invalid manifest"
            else
                meta = value
                url = resolve(manifest_url, value.image_url)
                if not url then error_message = "Invalid image URL" end
            end
        end
        if not error_message then
            local old = self:readMetadata()
            if meta.sha256 and old.sha256 == meta.sha256 and self:fileExists(self:cacheImg()) then
                local f = io.open(self:cacheImg(), "rb")
                local bytes = f and f:read(MAX_IMAGE + 1)
                if f then f:close() end
                if bytes and #bytes <= MAX_IMAGE and (not sha or sha(bytes) == meta.sha256) then
                    self._candidate_meta = meta
                    return bytes, nil, ep.name, true
                end
            end
            local body, err = self:request(url, MAX_IMAGE)
            if body and body:sub(1, 8) == "\137PNG\13\10\26\10" then
                if not meta.sha256 or not sha or sha(body) == meta.sha256:lower() then
                    if sha then meta.sha256 = sha(body) end
                    meta.source = ep.name
                    self._candidate_meta = meta
                    return body, nil, ep.name, false
                end
                error_message = "Image checksum mismatch"
            else
                error_message = err or "Invalid PNG"
            end
        end
        failures[#failures + 1] = ep.name .. ": " .. tostring(error_message)
    end
    return nil, table.concat(failures, "; ")
end

-- 校验并落盘 PNG（校验头、尺寸、与清单一致性），避免半张图 / 坏图。
function KindleDash:writePng(path, bytes)
    if not bytes or #bytes > MAX_IMAGE or #bytes < 33 then return nil, "Image size invalid" end
    local function uint(offset)
        local a, b, c, d = bytes:byte(offset, offset + 3)
        return ((a * 256 + b) * 256 + c) * 256 + d
    end
    if bytes:sub(1, 8) ~= "\137PNG\13\10\26\10" or bytes:sub(13, 16) ~= "IHDR"
        or uint(17) > 4096 or uint(21) > 4096 then
        return nil, "Invalid PNG header"
    end
    self:ensureCacheDir()
    -- 临时文件必须唯一：UI 定时器 / RTC / resume 可能并发触发两次刷新，
    -- 若共用同一个 .pending，后写的那个会继续往已被 rename 走的文件里追加字节，
    -- 缓存图直接损坏（而且损坏会被持久化，之后一直显示坏图）。
    self._write_seq = (self._write_seq or 0) + 1
    local tmp = path .. ".pending." .. tostring(self._write_seq)
    local f = io.open(tmp, "wb")
    if not f then return nil, "Cache open failed" end
    local written = f:write(bytes)
    local closed = f:close()
    if not written or not closed then os.remove(tmp); return nil, "Cache write failed" end
    local ok, buffer = pcall(RenderImage.renderImageFile, RenderImage, tmp, false)
    if not ok or not buffer then os.remove(tmp); return nil, "Image decode failed" end
    local w, h = buffer:getWidth(), buffer:getHeight()
    buffer:free()
    if w < 100 or h < 100 or w > 4096 or h > 4096 then os.remove(tmp); return nil, "Invalid dimensions" end
    local meta = self._candidate_meta or {}
    if (meta.width and meta.width ~= w) or (meta.height and meta.height ~= h) then
        os.remove(tmp); return nil, "Manifest dimensions mismatch"
    end
    local renamed = os.rename(tmp, path)
    if not renamed then os.remove(tmp); return nil, "Cache rename failed" end
    meta.downloadedAt = os.time() * 1000
    local mf = io.open(path .. ".json.tmp", "wb")
    if mf then mf:write(JSON.encode(meta)); mf:close(); os.rename(path .. ".json.tmp", path .. ".json") end
    return true
end

-- ---------------- 显示 ----------------

function KindleDash:buildScreen(img_path, w, h)
    local dash = self
    local img = ImageWidget:new{
        file = img_path,
        width = w,
        height = h,
        scale_factor = 0,
        file_do_cache = false,
    }
    local container = InputContainer:new{ dimen = Geom:new{ w = w, h = h } }
    container[1] = img
    local battery = TextWidget:new{ text = "", face = Font:getFace("cfont"), bold = true }
    local margin = math.floor(w * 0.037)
    function container:updateBattery()
        local level = dash:batteryCapacity()
        local label = level and string.format("%s %d%%", dash:tr("电量"), level) or ""
        if battery.text == label then return end
        battery:setText(label)
        if dash.dash_widget == self then UIManager:setDirty(self, "ui", self.dimen) end
    end
    function container:paintTo(bb, x, y)
        InputContainer.paintTo(self, bb, x, y)
        if battery.text == "" or self.skip_paint then return end
        local size = battery:getSize()
        local left, top = x + w - margin - size.w, y + h - margin - size.h
        bb:paintRect(left - 6, top, size.w + 12, size.h, Blitbuffer.COLOR_WHITE)
        battery:paintTo(bb, left, top)
    end
    function container:onCloseWidget()
        battery:free()
    end
    container:updateBattery()
    -- 吃手势，防止 KOReader 的翻页/退出穿透到看板上。
    container.ges_events = {
        TapScroll = { GestureRange:new{ ges = "tap", range = function() return container.dimen end } },
        SwipeScroll = { GestureRange:new{ ges = "swipe", range = function() return container.dimen end } },
    }
    function container:onTapScroll(_, ges)
        -- 顶部 10% 区域点击 = 退出（Kindle 无 Back 键，靠此关闭看板）
        if ges and ges.pos and ges.pos.y < h * 0.1 then self:onClose() end
        return true
    end
    function container:onSwipeScroll(_, ges)
        -- 顶部 25% 下滑 = 退出
        if ges and ges.pos and ges.direction == "south" and ges.pos.y < h * 0.25 then self:onClose() end
        return true
    end
    function container:onClose()
        -- 先标记已关闭再关窗：否则后台刷新会误判成"看板正显示"。
        dash._generation = (dash._generation or 0) + 1
        dash._busy = false
        dash:releaseNetwork()
        dash.dash_widget = nil
        -- 看板不在了：停止定时刷新，也不再阻止设备休眠。
        dash:holdScreen(false)
        dash:armAutoRefresh()
        UIManager:close(self)
        return true
    end
    function container:onBack()
        dash._generation = (dash._generation or 0) + 1
        dash._busy = false
        dash:releaseNetwork()
        dash.dash_widget = nil
        dash:holdScreen(false)
        dash:armAutoRefresh()
        UIManager:close(self)
        return true
    end
    function container:onResume()
        dash:onResume()
        return true
    end
    function container:onSuspend()
        dash:onSuspend()
        return true
    end
    -- 只负责构建，不负责上屏：由 showDashboard 决定何时替换旧看板，
    -- 这样构建失败时旧看板仍然完好地留在屏幕上。
    return container
end

function KindleDash:showDashboard(path, offline)
    local previous = self.dash_widget
    local ok, result = pcall(self.buildScreen, self, path, Screen:getWidth(), Screen:getHeight())
    if not ok then
        self._last_error = "Display failed: " .. tostring(result)
        self:record("display_failed", self._last_error)
        UIManager:show(InfoMessage:new{ text = self:tr("看板显示失败:\n") .. tostring(result), timeout = 8 })
        return false
    end
    UIManager:show(result)
    if previous then UIManager:close(previous) end
    self.dash_widget = result
    -- 看板已在屏上：进入常亮模式，让 UI 定时器能按点刷新。
    if self:option("hold_screen", true) and not self._suspended then
        self:holdScreen(true)
    end
    return true
end

local function fmtTime(t)
    return t and os.date("%Y-%m-%d %H:%M:%S", t) or "—"
end

function KindleDash:showStatus()
    local m = self:readMetadata()
    local age = m.generatedAt and math.max(0, math.floor((os.time() * 1000 - m.generatedAt) / 60000))
    local text = self:label("Device status", "设备状态") .. " · " .. VERSION .. "\n"
        .. self:label("Screen: ", "屏幕：") .. Screen:getWidth() .. " × " .. Screen:getHeight() .. "\n"
        .. self:label("RTC wake: ", "RTC 唤醒：") .. (Device.wakeup_mgr and self:label("available", "可用") or self:label("unavailable", "不可用")) .. "\n"
        .. self:label("Hold awake: ", "常亮：") .. (self._holding and self:tr("开") or self:tr("关")) .. "\n"
        .. self:label("Source: ", "图源：") .. tostring(self._source or m.source or "—") .. "\n"
        .. self:label("Content generated: ", "内容生成：") .. fmtTime(m.generatedAt and m.generatedAt / 1000) .. "\n"
        .. self:label("Cloud state: ", "云端状态：") .. tostring(m.state or "Unknown / 未知") .. "\n"
        .. self:label("Freshness: ", "新鲜度：") .. (not age and self:label("Unknown", "未知") or age > 45 and self:label("Stale", "已过期") or self:label("Within 45 minutes", "45分钟内")) .. "\n"
        .. self:label("Age (min): ", "内容年龄（分钟）：") .. tostring(age or "Unknown / 未知") .. "\n"
        .. self:label("Downloaded: ", "下载时间：") .. fmtTime(m.downloadedAt and m.downloadedAt / 1000) .. "\n"
        .. self:label("Last attempt: ", "最近尝试：") .. fmtTime(self._last_attempt) .. "\n"
        .. self:label("Next attempt: ", "下次尝试：") .. fmtTime(self._next_attempt) .. "\n"
        .. self:label("Last error: ", "最近错误：") .. tostring(self._last_error or "—") .. "\n"
        .. self:label("Health log: settings/kindledash-health.json", "诊断记录：settings/kindledash-health.json")
    UIManager:show(InfoMessage:new{ text = text })
end

-- ---------------- 刷新流程 ----------------

-- Kindle 空闲会自动关 WiFi，RTC 唤醒后通常也没网。取图前先确保网络可用，
-- 否则只会一直显示"离线"。受管 WiFi：需要时才开，超时后关掉自己开的 WiFi。
local NetworkMgr

function KindleDash:networkMgr()
    if NetworkMgr ~= nil then return NetworkMgr end
    local ok, mod = pcall(require, "ui/network/manager")
    if ok and mod then NetworkMgr = mod end
    return NetworkMgr
end

-- 返回 true 表示现在就能取图（已同步调用 on_ready 或无需等待）；
-- 返回 false 表示已安排超时/回调，稍后由它们继续。
-- 这里只管网络，不碰 _busy / 重试——那些由调用方按 generation 决定，
-- 否则被抢占的旧刷新会把新刷新的状态一起清掉。
function KindleDash:releaseNetwork()
    if self._cancel_fetch then self._cancel_fetch() end
    if self._network_release then self._network_release() end
end

function KindleDash:prepareNetwork(on_ready, on_fail)
    self:releaseNetwork()
    local net = self:networkMgr()
    if not net or not self:option("managed_wifi", false) then return true end
    if net:isConnected() then return true end
    if self._network_deadline then
        UIManager:unschedule(self._network_deadline)
        self._network_deadline = nil
    end
    local owned = net.isWifiOn and not net:isWifiOn()
    local settled, released = false, false
    local deadline
    local release
    release = function()
        if released then return end
        released, settled = true, true
        if deadline then UIManager:unschedule(deadline) end
        if self._network_deadline == deadline then self._network_deadline = nil end
        if self._network_release == release then self._network_release = nil end
        if owned and self:option("wifi_off", false) then pcall(function() net:disableWifi() end) end
    end
    self._network_release = release
    local function settle(ok)
        if settled then return end
        settled = true
        if self._network_deadline then
            UIManager:unschedule(self._network_deadline)
            self._network_deadline = nil
        end
        if ok then
            on_ready()
            return
        end
        -- 超时：放弃本次，顺手关掉自己开的 WiFi，然后交给调用方退避重试。
        release()
        on_fail()
    end
    deadline = function() settle(false) end
    self._network_deadline = deadline
    UIManager:scheduleIn(60, self._network_deadline)
    local ok = pcall(function()
        net:turnOnWifiAndWaitForConnection(function() settle(true) end)
    end)
    if not ok then settle(false) end
    return false
end

-- DNS and HTTPS may block inside LuaSocket. Keep them out of the UI process.
function KindleDash:startFetch(done)
    local prefix = self:cacheDir() .. "/.daypane-fetch-"
    local language = self.language
    local pid = ffiutil.runInSubProcess(function(child)
        self._candidate_meta = nil
        local data, err, source, unchanged = self:fetchScreen()
        local header = JSON.encode({ error = err, source = source, unchanged = unchanged,
                                     meta = self._candidate_meta, ok = data ~= nil })
        local f = assert(io.open(prefix .. child, "wb"))
        f:write(header, "\n", data or "")
        f:close()
    end)
    if not pid then return done(nil, "Unable to start image download") end
    local path = prefix .. pid
    local deadline = time.now() + time.s(25)
    local poll
    local function reap()
        if ffiutil.isSubProcessDone(pid) then
            os.remove(path)
        else
            UIManager:scheduleIn(0.2, reap)
        end
    end
    local function cancel()
        self._cancel_fetch = nil
        UIManager:unschedule(poll)
        if not ffiutil.isSubProcessDone(pid) then ffiutil.terminateSubProcess(pid) end
        reap()
    end
    poll = function()
        if ffiutil.isSubProcessDone(pid) then
            self._cancel_fetch = nil
            local f = io.open(path, "rb")
            if not f then return done(nil, "Image download returned no result") end
            local raw = f:read("*l")
            local data = f:read(MAX_IMAGE + 1)
            f:close()
            os.remove(path)
            if self.language ~= language then return done(nil, "Image language changed") end
            local ok, result = pcall(JSON.decode, raw or "")
            if not ok or type(result) ~= "table" then return done(nil, "Invalid image download result") end
            self._candidate_meta = result.meta
            return done(result.ok and data or nil, result.error, result.source, result.unchanged)
        end
        if time.now() >= deadline then
            cancel()
            return done(nil, "Image download timed out")
        end
        UIManager:scheduleIn(0.25, poll)
    end
    self._cancel_fetch = cancel
    UIManager:scheduleIn(0.25, poll)
end

function KindleDash:refreshDashboard(silent, manual, data, err, source, unchanged)
    if self._suspended then return false end
    self._fetch_error = err
    local cacheImg = self:cacheImg()
    local showing = (self.dash_widget ~= nil)   -- 看板此刻是否正显示在屏幕上

    if not data then
        -- 拉取失败：看板正显示时（或用户主动打开时）用持久缓存顶上
        if self:fileExists(cacheImg) then
            self._offline = true
            self._last_ok = true
            if showing or manual then
                self:showDashboard(cacheImg, true)
                if not silent then
                    local ts = self:readTs()
                    local msg = ts and (self:tr("离线 · 最后 ") .. ts) or self:tr("离线 · 显示上次缓存")
                    UIManager:show(InfoMessage:new{ text = msg, timeout = 2 })
                end
            end
        elseif not silent then
            UIManager:show(InfoMessage:new{ text = self:tr("刷新失败: ") .. tostring(err), timeout = 3 })
        end
        logger.warn("ShawnKanban refresh failed:", err)
        return false
    end

    -- 成功：写持久缓存 + 时间戳
    local written, write_error = true, nil
    if not unchanged then written, write_error = self:writePng(cacheImg, data) end
    if not written then
        self._fetch_error = write_error
        logger.warn("ShawnKanban cache write failed")
        return false
    end
    self:writeTs(os.date("%Y-%m-%d %H:%M"))
    self._offline = false
    self._last_ok = true
    self._source = source

    -- 后台刷新且看板没在显示：只默默更新缓存，别把看板弹回来（下次打开即是最新）
    if not showing and not manual then
        logger.info("ShawnKanban bg refresh ok source=", source)
        return true
    end

    if unchanged and showing then return true end
    if self:showDashboard(cacheImg, false) == false then
        self._fetch_error = self._last_error
        return false
    end
    if not silent and source == self:tr("云端") then
        UIManager:show(InfoMessage:new{ text = self:tr("来自云端（电脑未连上）"), timeout = 2 })
    end
    return true
end

-- 带防重入的刷新入口（UI 定时器 / RTC / 用户操作共用）。
-- 无论走哪条分支，_busy 一定会被释放，否则自动刷新会永久停摆。
function KindleDash:requestRefresh(silent, manual)
    if self._suspended then return false end
    if self._busy then
        -- 手动刷新必须能抢过后台刷新：否则用户在后台刷新期间点"刷新看板"毫无反应
        -- （旧实现直接 return，连个提示都没有）。抢占只需换 generation，
        -- 进行中的那次会在 settle/proceed 里发现号不对而自行退出。
        if not manual then return false end
    end
    if self.dash_widget and self.dash_widget.updateBattery then self.dash_widget:updateBattery() end
    self._busy = true
    self._last_attempt = os.time()
    local generation = (self._generation or 0) + 1
    self._generation = generation
    self:record("refresh_started")
    local function settle(ok, err)
        if generation ~= self._generation then return end
        self._busy = false
        self:releaseNetwork()
        if ok then self._last_error = nil else self._last_error = tostring(err or "Refresh failed") end
        self._failures = ok and 0 or (self._failures or 0) + 1
        self:record(ok and "download_verified" or "refresh_failed", self._last_error)
        if ok then self:armAutoRefresh() else self:armAutoRefresh(self:retryDelay()) end
    end
    local function proceed()
        if generation ~= self._generation then return end
        local started, err = pcall(self.startFetch, self, function(data, failure, source, unchanged)
            if generation ~= self._generation then return end
            local ok, result = pcall(self.refreshDashboard, self, silent, manual, data, failure, source, unchanged)
            settle(ok and result, ok and self._fetch_error or result)
        end)
        if not started then settle(false, err) end
    end
    local function fail_network()
        if generation ~= self._generation then return end
        self._busy = false
        self._failures = (self._failures or 0) + 1
        self._last_error = "Network timeout"
        self:record("network_timeout")
        self:armAutoRefresh(self:retryDelay())
    end
    if self:prepareNetwork(proceed, fail_network) then proceed() end
    return true
end

-- 菜单入口：任何异常都收敛成提示，避免把 KOReader 打回桌面。
function KindleDash:safeRefresh()
    local ok, err = pcall(function() self:requestRefresh(false, true) end)
    if not ok then
        logger.err("ShawnKanban refresh crashed: ", tostring(err))
        UIManager:show(InfoMessage:new{ text = self:tr("看板失败:\n") .. tostring(err), timeout = 8 })
    end
end

-- ---------------- 常亮保活 ----------------

local PluginShare

local function pluginShare()
    if PluginShare ~= nil then return PluginShare end
    local ok, mod = pcall(require, "pluginshare")
    if ok and type(mod) == "table" then PluginShare = mod else PluginShare = false end
    return PluginShare
end

-- 看板显示期间防止设备休眠（与 keepalive 插件同款双通道，真机已验证）：
--   1) PluginShare.pause_auto_suspend：停掉 AutoSuspend 插件的挂起倒计时；
--   2) lipc preventScreenSaver：挡住固件层的屏保/休眠。
-- 为什么不用 RTC 定时唤醒：PW3 实测闹钟会响、设备会醒，但唤醒滞后 ~90-100s，
-- 超过 WakeupMgr:wakeupAction(90) 的邻近窗口 → 任务被拒收、刷新不执行，
-- 且每次无谓唤醒还耗电。常亮 + UI 定时器在这台设备上是唯一可靠路径。
function KindleDash:holdScreen(on)
    if self._holding == on then return end
    self._holding = on
    local ps = pluginShare()
    if ps then ps.pause_auto_suspend = on end
    pcall(os.execute, "lipc-set-prop com.lab126.powerd preventScreenSaver " .. (on and "1" or "0"))
    self:record(on and "hold_screen_on" or "hold_screen_off")
end

function KindleDash:toggleHoldScreen()
    local on = not self:option("hold_screen", true)
    self:setOption("hold_screen", on)
    if on and self.dash_widget and not self._suspended then
        self:holdScreen(true)
    else
        self:holdScreen(false)
    end
    UIManager:show(InfoMessage:new{
        text = on and self:tr("常亮: 开（看板显示期间设备不休眠）") or self:tr("常亮: 关"),
        timeout = 3,
    })
end

-- ---------------- 生命周期 ----------------

function KindleDash:onSuspend()
    self:releaseNetwork()
    self._suspended = true
    if self._resume_tick then UIManager:unschedule(self._resume_tick) end
    if self._auto_timer then UIManager:unschedule(self._auto_timer) end
    if self._network_deadline then
        UIManager:unschedule(self._network_deadline)
        self._network_deadline = nil
    end
    -- 刷新途中被挂起（RTC 唤醒后很常见）：必须释放锁并作废进行中的那次。
    -- 否则 _busy 永久为 true，此后所有 requestRefresh 直接 return，自动刷新彻底停摆。
    self._busy = false
    self._generation = (self._generation or 0) + 1
    -- 用户主动按键休眠必须放行：清掉常亮，否则固件挡着睡不下去。
    -- 醒来后 onResume 会按配置重新上常亮。
    self:holdScreen(false)
    self:scheduleRtcWake()
    local armed
    if self:option("rtc_wake", false) and Device.wakeup_mgr then
        local ok, scheduled = pcall(function() return Device.wakeup_mgr:isWakeupAlarmScheduled() end)
        armed = ok and scheduled
    else
        armed = "hold"
    end
    -- 这条记录是"到底会不会自动刷新"的唯一证据：hold=常亮模式（醒着定时刷），
    -- "rtc armed"=RTC 唤醒模式已上弦，"rtc NOT armed"=唤醒链断了（设备睡下去不会再自己醒）。
    self:record("suspend", armed == "hold" and "hold-screen mode" or (armed and "rtc armed" or "rtc NOT armed"))
end

function KindleDash:onResume()
    self._suspended = false
    self._busy = false
    self._generation = (self._generation or 0) + 1
    if self.dash_widget and self.dash_widget.updateBattery then self.dash_widget:updateBattery() end
    if self._resume_tick then UIManager:unschedule(self._resume_tick) end
    if self._network_deadline then
        UIManager:unschedule(self._network_deadline)
        self._network_deadline = nil
    end
    -- 走 requestRefresh：它自带防重入、受管 Wi-Fi（唤醒后 WiFi 通常是关的）和失败退避。
    -- 不要在这里造第二套重试——两套重试会并发写缓存。
    -- 同时恢复常亮：看板还在屏上的话，设备醒着才能继续定时刷新。
    self._resume_tick = function()
        if self.dash_widget and not self._suspended then
            if self:option("hold_screen", true) then self:holdScreen(true) end
            self:requestRefresh(true, false)
        end
    end
    -- Wi-Fi 恢复是异步的，稍等再刷。
    UIManager:scheduleIn(5, self._resume_tick)
    self:armAutoRefresh()
    self:record("resume")
end

function KindleDash:onCloseWidget()
    self._generation = (self._generation or 0) + 1
    self:releaseNetwork()
    -- 只收定时器，不改 auto_on：这个回调不只退出时触发（插件从菜单注销等也会），
    -- 一旦把 auto_on 置 false，自动刷新会静默关停到下次重启，且没有任何提示。
    if self._auto_timer then UIManager:unschedule(self._auto_timer); self._auto_timer = nil end
    if self._resume_tick then UIManager:unschedule(self._resume_tick) end
    if self._network_deadline then
        UIManager:unschedule(self._network_deadline)
        self._network_deadline = nil
    end
    self._busy = false
    self:holdScreen(false)
    self:cancelRtcWake()
end

function KindleDash:init()
    self.language = self:loadLanguage()
    self.auto_on = self:option("auto_refresh", true)
    self.host = self:loadHost()
    self.cloud = self:loadCloud()
    self.dash_widget = nil
    self._auto_timer = nil
    self._resume_tick = nil
    self._rtc_task = nil
    self._deferred = nil
    self._network_deadline = nil
    self._holding = false
    self._suspended = false
    self._busy = false
    self._last_ok = false
    self._offline = false
    self._source = nil
    local f = io.open(self:cacheDir() .. "/kindledash-health.json", "rb")
    if f then
        local raw = f:read("*a"); f:close()
        local ok, value = pcall(JSON.decode, raw)
        if ok and type(value) == "table" then self._health_history = value end
    end
    -- 不在这里开自动刷新：此刻看板还没上屏，armAutoRefresh 会直接跳过。
    -- 用户打开看板（菜单"刷新看板"）时自然就会启动。
    self.ui.menu:registerToMainMenu(self)
end

-- ---------------- 设置对话框 ----------------

function KindleDash:setServerAddress()
    local dialog
    dialog = InputDialog:new{
        title = self:tr("服务器地址 (IP:端口)"),
        input = self.host,
        input_hint = self:tr("例如 192.168.31.188:8787"),
        buttons = {
            {
                { text = self:tr("取消"), callback = function() UIManager:close(dialog) end },
                { text = self:tr("保存"), callback = function()
                    local v = dialog:getInputValue()
                    if v ~= nil then
                        self:saveHost(v)
                        UIManager:close(dialog)
                        UIManager:show(InfoMessage:new{ text = self:tr("已保存: ") .. v, timeout = 2 })
                    end
                end },
            },
        },
    }
    UIManager:show(dialog)
end

function KindleDash:setCloudUrl()
    local dialog
    dialog = InputDialog:new{
        title = self:tr("云端图地址 (完整 URL)"),
        description = self:tr("电脑关机时从这儿取图。留空则只用局域网。"),
        input = self.cloud or "",
        input_hint = self:tr("https://用户名.github.io/daypane/screen.png"),
        buttons = {
            {
                { text = self:tr("取消"), callback = function() UIManager:close(dialog) end },
                { text = self:tr("保存"), callback = function()
                    local v = dialog:getInputValue() or ""
                    self:saveCloud(v)
                    UIManager:close(dialog)
                    UIManager:show(InfoMessage:new{
                        text = v == "" and self:tr("已清空云端地址") or self:tr("已保存: ") .. v, timeout = 3,
                    })
                end },
            },
        },
    }
    UIManager:show(dialog)
end

function KindleDash:setupWizard()
    local dialog
    dialog = InputDialog:new{
        title = self:tr("设置：图片或清单地址"),
        description = self:tr("使用自己的图源或内置示例。城市、时区和布局在生成端配置。保存后测试图片。"),
        input = self.cloud or "",
        buttons = {
            {
                { text = self:tr("取消"), callback = function() UIManager:close(dialog) end },
                { text = self:tr("保存并测试"), callback = function()
                    local v = dialog:getInputValue() or ""
                    if v:match("^https?://") then
                        self:saveCloud(v)
                        self:setOption("setup_complete", true)
                        UIManager:close(dialog)
                        self:safeRefresh()
                    end
                end },
            },
        },
    }
    UIManager:show(dialog)
end

function KindleDash:editInterval()
    local dialog
    dialog = InputDialog:new{
        title = self:tr("刷新间隔（分钟，5–1440）"),
        input = tostring((self:option("interval", REFRESH_SEC)) / 60),
        buttons = {
            {
                { text = self:tr("取消"), callback = function() UIManager:close(dialog) end },
                { text = self:tr("保存"), callback = function()
                    local n = tonumber(dialog:getInputValue())
                    if n and n >= 5 and n <= 1440 then
                        self:setOption("interval", math.floor(n) * 60)
                        UIManager:close(dialog)
                        self:armAutoRefresh()
                    end
                end },
            },
        },
    }
    UIManager:show(dialog)
end

function KindleDash:rtcExperiment()
    local Confirm = require("ui/widget/confirmbox")
    if not Device.wakeup_mgr or not Device.canSuspend or not Device:canSuspend() then
        UIManager:show(InfoMessage:new{ text = self:tr("此设备不支持 RTC 唤醒接口。") })
        return
    end
    UIManager:show(Confirm:new{
        text = self:tr("实验：休眠一次并尝试在2分钟后唤醒。请确保可按电源键恢复。不会开启循环休眠。"),
        ok_callback = function()
            if self._rtc_test then Device.wakeup_mgr:removeTasks(nil, self._rtc_test) end
            self._rtc_test = function()
                self._suspended = false
                self:record("rtc_alarm_fired")
                self:onResume()
            end
            Device.wakeup_mgr:addTask(120, self._rtc_test)
            self:record("rtc_test_started")
            UIManager:suspend()
        end,
    })
end

-- ---------------- 菜单 ----------------

function KindleDash:addToMainMenu(menu_items)
    menu_items["0kindledash"] = {
        text = "DayPane",
        sorting_hint = "tools",
        sub_item_table_func = function() return {
            { text = "Language / 语言", sub_item_table = {
                { text = "English", checked_func = function() return self.language == "en" end,
                  callback = function() self:setLanguage("en") end },
                { text = "中文", checked_func = function() return self.language == "zh" end,
                  callback = function() self:setLanguage("zh") end },
            } },
            { text = self:tr("刷新看板"), callback = function() self:safeRefresh() end },
            { text = self:tr("设置局域网服务器"), callback = function() self:setServerAddress() end },
            { text = self:tr("设置云端图地址"), callback = function() self:setCloudUrl() end },
            { text = self:tr("切换自动刷新 (整点/半点)"), callback = function() self:toggleAutoRefresh() end },
            { text = self:tr("常亮看板模式（看板显示时防休眠）"),
              checked_func = function() return self:option("hold_screen", true) end,
              callback = function() self:toggleHoldScreen() end },
            { text = self:tr("设置：图片或清单地址"), callback = function() self:setupWizard() end },
            { text = self:tr("设备状态"), callback = function() self:showStatus() end },
            { text = self:tr("刷新间隔"), callback = function() self:editInterval() end },
            { text = self:label("Connect Wi-Fi for updates", "更新时连接 Wi-Fi"),
              checked_func = function() return self:option("managed_wifi", false) end,
              callback = function() self:setOption("managed_wifi", not self:option("managed_wifi", false)) end },
            { text = self:label("Turn off Wi-Fi started by dashboard", "关闭看板开启的 Wi-Fi"),
              checked_func = function() return self:option("wifi_off", false) end,
              callback = function() self:setOption("wifi_off", not self:option("wifi_off", false)) end },
            { text = self:tr("实验：单次 RTC 唤醒测试"), callback = function() self:rtcExperiment() end },
            { text = self:tr("关于"), callback = function()
                UIManager:show(InfoMessage:new{
                    text = "DayPane v" .. VERSION .. "\n"
                       .. self:label("Image sources: LAN > Cloud > Cache\n", "取图顺序：局域网 PC > 云端 Pages > 本地缓存\n")
                       .. self:tr("看板显示期间保持常亮并定时刷新\n")
                       .. self:tr("AI 额度走局域网实时，关机显示最后值\n")
                       .. self:tr("顶部下滑/顶部点击返回看板退出"),
                    timeout = 6,
                })
            end },
        } end,
    }
end

return KindleDash
