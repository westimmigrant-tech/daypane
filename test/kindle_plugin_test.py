"""Behavioral regression tests with a minimal KOReader API simulator (requires lupa).

Every KOReader module stub lives here, because `main.lua` resolves `require()` once
when it is loaded. Test modules that need other runtime state (network up/down,
decode failure, sample images) mutate the globals defined below instead of adding
new `package.preload` entries, which would silently have no effect.
"""
from pathlib import Path
from lupa import LuaRuntime
lua = LuaRuntime()
lua.execute('''
local base = {}
function base:new(o) return setmetatable(o or {}, {__index=self}) end
unpack = table.unpack

-- sha256.lua runs on LuaJIT's `bit` library.
package.preload['bit'] = function()
    local function signed(x) x=x & 0xffffffff;return x>=0x80000000 and x-0x100000000 or x end
    return {tobit=signed,band=function(a,b) return signed(a & b) end,
        bxor=function(a,b,c) local n=a ~ b;if c then n=n ~ c end;return signed(n) end,
        bnot=function(a) return signed(~a) end,rshift=function(a,n) return (a & 0xffffffff)>>n end,
        ror=function(a,n) a=a & 0xffffffff;return signed((a>>n)|(a<<(32-n))) end,
        tohex=function(a) return string.format('%08x',a & 0xffffffff) end}
end

-- Manifests: decode() only understands the literal 'manifest' used by the fakes.
DECODE_FAIL = false
package.preload['json'] = function() return {
    decode=function(raw) if raw=='manifest' then return TEST_META end; error('bad JSON') end,
    encode=function() return '{}' end } end

-- UIManager: an inspectable timer queue instead of a real event loop.
local queue = {}
UI = {queue=queue}
function UI:scheduleIn(delay, fn) queue[fn] = delay end
function UI:unschedule(fn) queue[fn] = nil end
function UI:show() end
function UI:close() end
function UI:suspend() end
function UI:setDirty(widget) self.dirty=widget end

PS = {}
POWER = {resets=0}
function POWER:getCapacity() return self.capacity end
function POWER:resetT1Timeout() self.resets=self.resets+1 end
function POWER:isCharging() return false end
function POWER:isCharged() return false end

-- os.execute: record lipc calls so hold-awake tests can inspect them.
EXEC = {commands={}}
function os.execute(cmd) table.insert(EXEC.commands, cmd); return true end

-- Per-instance settings backing store (pluginshare-style preferences).
PREFS = {values={}}
function PREFS:readSetting(key) return self.values[key] end
function PREFS:saveSetting(key, v) self.values[key] = v end
function PREFS:flush() end

-- RTC wake manager (Device.wakeup_mgr): the plugin's auto-refresh depends on it.
local wake = {tasks={}}
WAKEUP = wake
function wake:addTask(delay, fn) table.insert(self.tasks, {delay=delay, fn=fn}) end
function wake:removeTasks(_, fn)
    for i=#self.tasks,1,-1 do
        if fn == nil or self.tasks[i].fn == fn then table.remove(self.tasks, i) end
    end
end
function wake:isWakeupAlarmScheduled() return #self.tasks > 0 end

-- Wi-Fi manager (ui/network/manager).
NET = {connected=true,on=true,disabled=false}
function NET:isConnected() return self.connected end
function NET:isWifiOn() return self.on end
function NET:turnOnWifiAndWaitForConnection(cb) self.callback=cb end
function NET:disableWifi() self.disabled=true end
package.preload['ui/network/manager'] = function() return NET end

-- Device / screen (1072x1448 = Kindle PW3 portrait).
local device = {screen={},wakeup_mgr=wake}
function device:isKindle() return true end
function device:canSuspend() return true end
function device:getPowerDevice() return POWER end
function device.screen.getWidth() return 1072 end
function device.screen.getHeight() return 1448 end
package.preload['device'] = function() return device end

-- RenderImage: DECODE_FAIL simulates a corrupted PNG that Pillow/KOReader rejects.
package.preload['ui/renderimage'] = function() return {renderImageFile=function()
    if DECODE_FAIL then return nil end
    return {getWidth=function() return 1072 end,getHeight=function() return 1448 end,free=function() end}
end} end

HTTP = {}
function HTTP.request(req)
    assert(type(req)=='table' and req.sink)
    req.sink('\\137PNGrest')
    return 1, 200
end
package.preload['pluginshare'] = function() return PS end
package.preload['socket.http'] = function() return HTTP end
package.preload['ltn12'] = function() return {sink={table=function(t)
    return function(chunk) if chunk then table.insert(t,chunk) end return 1 end
end}} end
package.preload['datastorage'] = function() return {getDataDir=function() return '/tmp' end} end
package.preload['logger'] = function() return {info=function() end,warn=function() end,err=function() end} end
package.preload['ui/uimanager'] = function() return UI end
package.preload['ui/font'] = function() return {getFace=function() return {} end} end
package.preload['ffi/blitbuffer'] = function() return {COLOR_WHITE='white'} end
package.preload['ui/widget/textwidget'] = function() return base:new{
    setText=function(self, text) self.text=text end,
    getSize=function(self) return {w=120,h=28} end,
    paintTo=function(self, bb, x, y) bb.label={text=self.text,x=x,y=y} end,
    free=function() end
} end
package.preload['ui/widget/container/inputcontainer'] = function() return base:new{
    paintTo=function(_, bb) bb.image=true end
} end
for _, name in ipairs({'ui/widget/container/widgetcontainer','ui/widget/infomessage',
'ui/widget/inputdialog','ui/widget/imagewidget',
'ui/geometry','ui/gesturerange','luasettings','libs/libkoreader-lfs'}) do
    package.preload[name]=function() return base end
end
''')

PLUGIN_DIR = Path(__file__).parents[1] / 'KindleDash.koplugin'
lua.globals().PLUGIN_DIR = str(PLUGIN_DIR) + '/'
lua.globals().Plugin = lua.execute((PLUGIN_DIR / 'main.lua').read_text())

lua.execute('''
local d = Plugin:new{auto_on=true}
d.fileExists=function() return true end
d.dash_widget = {}
d.preferences = function() return PREFS end
-- Plugin:new() does not run init() (KOReader's PluginLoader does); mirror the
-- lifecycle fields it would set so assertions check real hold-state transitions.
d._holding = false
d._suspended = false
d._busy = false
local refreshes = 0
d.refreshDashboard=function(self) assert(self==d); refreshes=refreshes+1; return true end
assert(d:request('https://example.test/screen.png', 1024) == '\\137PNGrest')
HTTP.request=function() return nil, 'timeout' end
assert(d:request('https://example.test/screen.png', 1024) == nil)
-- Default profile (rtc_wake off after the PW3 field test): auto-refresh runs
-- purely on UI timers and never arms RTC wake tasks.
d:armAutoRefresh()
assert(UI.queue[d._auto_timer] ~= nil and #WAKEUP.tasks == 0)
local old = d._auto_timer
d:toggleAutoRefresh()
assert(UI.queue[old] == nil and #WAKEUP.tasks == 0)
d:toggleAutoRefresh()
assert(UI.queue[old] == nil and UI.queue[d._auto_timer] and #WAKEUP.tasks == 0)
-- Showing the dashboard engages hold-awake: AutoSuspend paused and the firmware
-- screensaver blocked via lipc (the two channels the keepalive plugin uses).
assert(d._holding == false and PS.pause_auto_suspend == nil and #EXEC.commands == 0)
assert(d:showDashboard('/tmp/hold.png', false) == true)
assert(d._holding == true and PS.pause_auto_suspend == true)
assert(EXEC.commands[#EXEC.commands] == 'lipc-set-prop com.lab126.powerd preventScreenSaver 1')
-- A manual power-key suspend must release the hold (otherwise the firmware
-- would refuse to sleep); resume re-engages it because the dashboard is still up.
d:onSuspend()
assert(d._holding == false and PS.pause_auto_suspend == false)
d:onResume()
local retry = d._resume_tick
UI.queue[retry]=nil; retry()
assert(d._holding == true and PS.pause_auto_suspend == true and refreshes == 1)
UI.queue[retry]=nil; retry()
assert(refreshes == 2 and UI.queue[retry]==nil)
-- Suspending mid-refresh must release the re-entrancy lock. If it stayed set,
-- every later requestRefresh() would return immediately and auto-refresh would
-- be dead until a restart -- the very symptom this plugin is supposed to fix.
d:requestRefresh(true, false)
d:onSuspend()
assert(d._busy == false and d._holding == false)
d:onResume()
assert(d._busy == false)
-- A manual refresh must be able to preempt an in-flight background refresh;
-- silently dropping it leaves the user staring at a menu item that does nothing.
d._busy = true
assert(d:requestRefresh(true, false) == false)
assert(d:requestRefresh(false, true) == true)
d._busy = false
-- A failed build must leave the on-screen dashboard untouched: closing it
-- first and restoring the reference afterwards would claim a closed widget
-- is still displayed, and the device would look stuck.
local before = d.dash_widget
d.buildScreen = function() error('bad render') end
assert(d:showDashboard('/tmp/never.png', false) == false and d.dash_widget == before)
-- Closing the dashboard releases the hold and stops the UI timer.
d.dash_widget = nil
d:armAutoRefresh()
assert(UI.queue[d._auto_timer] == nil and d._holding == false and PS.pause_auto_suspend == false)
d:onCloseWidget()
assert(UI.queue[d._auto_timer]==nil and UI.queue[d._resume_tick]==nil)
-- RTC wake chain (opt-in): armed while the dashboard is up, re-armed only via
-- the deferred tick because WakeupMgr:wakeupAction() calls removeTask(1) right
-- after the callback returns, which would delete a synchronously queued task.
d.preferences = function()
    local p = {values={rtc_wake=true}}
    function p:readSetting(key) return self.values[key] end
    function p:saveSetting(key, v) self.values[key] = v end
    function p:flush() end
    return p
end
d.dash_widget = {}
d:armAutoRefresh()
assert(#WAKEUP.tasks == 1 and d._rtc_task ~= nil)
local first = d._rtc_task
d:onSuspend()
assert(#WAKEUP.tasks == 1 and d._rtc_task ~= first and WAKEUP.tasks[1].fn == d._rtc_task)
WAKEUP.tasks[1].fn()
local pending = d._deferred
assert(pending ~= nil and UI.queue[pending] == 0)
local seen = refreshes
UI.queue[pending] = nil; pending()
assert(refreshes == seen + 1 and #WAKEUP.tasks == 1 and WAKEUP.tasks[1].fn == d._rtc_task)
d:onCloseWidget()
assert(#WAKEUP.tasks == 0 and d._rtc_task == nil)
print('PASS: HTTPS bytes, network failure, timer cancellation, hold-awake channels, suspend, resume retry, dashboard close, opt-in RTC chain, cleanup')
''')

lua.execute('''
local saved = {}
local settings = {}
function settings:readSetting(key) return saved[key] end
function settings:has(key) return saved[key] ~= nil end
function settings:saveSetting(key, value) saved[key] = value end
function settings:flush() end
require('luasettings').open = function() return settings end
G_reader_settings = {readSetting=function() return 'C' end}
local d = Plugin:new{}
d.settingsPath=function() return '/tmp/settings' end
d.cacheDir=function() return '/tmp' end
assert(d:loadLanguage() == 'en' and saved.language == 'en')
d.language='en'
assert(d:loadCloud():match('screen%-en.png$'))
assert(d:tr('刷新看板') == 'Refresh dashboard')
local menus={}; d:addToMainMenu(menus)
assert(menus['0kindledash'].sub_item_table_func()[2].text == 'Refresh dashboard')
d.host='example.test:8787'; d.cloud=d:loadCloud()
assert(d:endpoints()[1].url:match('/api/screen') and d:endpoints()[1].url:match('lang=en$'))
local en_cache=d:cacheImg()
d:setLanguage('zh')
assert(saved.language=='zh' and d.cloud:match('/screen.png$'))
assert(d:cacheImg() ~= en_cache)
assert(menus['0kindledash'].sub_item_table_func()[2].text == '刷新看板')
d.cloud='https://example.test/custom.png'; d:setLanguage('en')
assert(d.cloud=='https://example.test/custom.png')
saved={host='old-device'}
assert(d:loadLanguage()=='zh' and saved.language=='zh')
saved={}; G_reader_settings.readSetting=function() return 'zh_CN' end
assert(d:loadLanguage()=='zh')
for _, filename in ipairs({'screen.png', 'screen-en.png'}) do
    saved={cloud='https://shenliucn-prog.github.io/shawn-kanban/'..filename}
    local expected='https://shenliucn-prog.github.io/daypane/'..filename
    assert(d:loadCloud()==expected and saved.cloud==expected)
    assert(d:loadCloud()==expected, 'migration must be idempotent')
end
for _, url in ipairs({'', 'https://example.test/custom.png',
    'https://shenliucn-prog.github.io/shawn-kanban/custom.png'}) do
    saved={cloud=url}; assert(d:loadCloud()==url and saved.cloud==url)
end
saved={}
print('PASS: legacy built-in URL migration preserves custom and empty URLs')
print('PASS: English first install, persisted locale, Chinese migration, dynamic menus, custom URLs, separate caches')
''')
