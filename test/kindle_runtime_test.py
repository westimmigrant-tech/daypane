"""Exercise the installed runtime using KOReader boundaries, without device power actions."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from PIL import Image
import kindle_plugin_test as harness

lua = harness.lua
lua.execute("SHA=dofile(PLUGIN_DIR..'sha256.lua'); Plugin.sha256=SHA")


class RuntimeTests(unittest.TestCase):
    def test_battery_overlay_updates_even_without_a_new_image(self):
        lua.execute('''
        local d=Plugin:new{language='zh'}
        d.option=function(_, key, default) return default end
        d.record=function() end
        d.cacheImg=function() return '/tmp/daypane-cache.png' end
        d.fileExists=function() return true end
        d.fetchScreen=function() return nil, 'offline' end
        POWER.capacity=76
        assert(not d:refreshDashboard(true,true,nil,'offline'))
        local widget=d.dash_widget
        local bb={paintRect=function(self,x,y,w,h,color)
            assert(self.image and x>=0 and y>=0 and x+w<=1072 and y+h<=1448 and color=='white')
        end}
        widget:paintTo(bb,0,0)
        assert(bb.label.text=='电量 76%' and bb.label.x>900 and bb.label.y>1350)

        d.fetchScreen=function() return true,nil,'Cloud',true end
        d.writeTs=function() end
        d.language='en'
        POWER.capacity=75
        assert(d:requestRefresh(true,true))
        assert(d.dash_widget==widget and UI.dirty==widget)
        widget:paintTo(bb,0,0)
        assert(bb.label.text=='Battery 75%')

        POWER.capacity=0
        d:onResume()
        widget:paintTo(bb,0,0)
        assert(bb.label.text=='Battery 0%')
        POWER.capacity=101
        widget:updateBattery()
        bb.label=nil
        widget:paintTo(bb,0,0)
        assert(bb.label==nil and bb.image)
        widget:onCloseWidget()
        d:onCloseWidget()
        POWER.capacity=nil
        ''')

    def test_stalled_download_times_out_and_can_be_cancelled(self):
        lua.execute('''
        local d=Plugin:new{language='en',startFetch=REAL_START_FETCH}
        d.cacheDir=function() return '/tmp' end
        local done, killed
        WORKER.runInSubProcess=function() return 7 end
        WORKER.isSubProcessDone=function() return killed end
        WORKER.terminateSubProcess=function(pid) assert(pid==7); killed=true end
        local function testTimeout(data, err)
            assert(data==nil and err=='Image download timed out')
            done=true
        end
        local old=UI.scheduleIn
        local poll
        UI.scheduleIn=function(self, delay, fn) assert(delay==0.25); poll=fn; old(self,delay,fn) end
        CLOCK=0
        d:startFetch(testTimeout)
        assert(poll and not done)
        CLOCK=26
        poll()
        assert(done and killed and not d._cancel_fetch)
        killed=false
        d:startFetch(function() error('cancelled download completed') end)
        d:releaseNetwork()
        assert(killed and not d._cancel_fetch and UI.queue[poll]==nil)
        UI.scheduleIn=old
        ''')

    def test_completed_download_passes_image_and_metadata_to_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            lua.globals().FETCH_DIR = tmp
            lua.execute('''
            local d=Plugin:new{language='en',startFetch=REAL_START_FETCH}
            d.cacheDir=function() return FETCH_DIR end
            d.fetchScreen=function(self)
                self._candidate_meta={generatedAt=123}
                return 'image bytes',nil,'Cloud',false
            end
            local j=require('json')
            local encode,decode=j.encode,j.decode
            j.encode=function(result)
                assert(result.ok and result.source=='Cloud' and result.meta.generatedAt==123)
                return '{}'
            end
            j.decode=function(raw)
                assert(raw=='{}')
                return {ok=true,source='Cloud',meta={generatedAt=123}}
            end
            WORKER.runInSubProcess=function(fn) fn(8); return 8 end
            WORKER.isSubProcessDone=function() return true end
            local done
            d:startFetch(function(bytes,err,source)
                assert(bytes=='image bytes' and not err and source=='Cloud')
                assert(d._candidate_meta.generatedAt==123 and not d._cancel_fetch)
                done=true
            end)
            assert(not done)
            local pending=d._cancel_fetch
            -- Only the new download poll is queued at 250 ms.
            for fn,delay in pairs(UI.queue) do
                if delay==0.25 then UI.queue[fn]=nil;fn() end
            end
            assert(pending and done)
            j.encode,j.decode=encode,decode
            ''')
            self.assertFalse(list(Path(tmp).iterdir()))

    def test_sha256_known_vectors(self):
        for data in [b'',b'abc',b'a'*1000,bytes(range(256))]:
            self.assertEqual(lua.globals().SHA(data), hashlib.sha256(data).hexdigest())

    def test_corrupt_download_and_decode_keep_cache(self):
        out=io.BytesIO();Image.new('L',(1072,1448),255).save(out,'PNG')
        lua.globals().PNG=out.getvalue()
        lua.globals().HASH=hashlib.sha256(out.getvalue()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            lua.globals().CACHE=tmp+'/screen.png'
            Path(tmp+'/screen.png').write_bytes(b'previous-good-image')
            lua.execute('''
            local d=Plugin:new{language='en'}
            d.ensureCacheDir=function() end
            d.cacheImg=function() return CACHE end
            d.readMetadata=function() return {} end
            d.endpoints=function() return {{url='https://example.test/manifest.json',name='Cloud'}} end
            TEST_META={schemaVersion=1,sha256=HASH,generatedAt=os.time()*1000,image_url='image.png',width=1072,height=1448}
            d.request=function(_,url) if url:match('json$') then return 'manifest' end;return PNG..'bad' end
            assert(d:fetchScreen()==nil)
            local f=io.open(CACHE,'rb');assert(f:read('*a')=='previous-good-image');f:close()
            d.request=function(_,url) return url:match('json$') and 'manifest' or PNG end
            local bytes=d:fetchScreen();assert(bytes==PNG)
            DECODE_FAIL=true;assert(not d:writePng(CACHE,bytes))
            f=io.open(CACHE,'rb');assert(f:read('*a')=='previous-good-image');f:close()
            DECODE_FAIL=false
            local rename=os.rename
            os.rename=function() return nil, 'simulated filesystem failure' end
            assert(not d:writePng(CACHE,bytes))
            os.rename=rename
            f=io.open(CACHE,'rb');assert(f:read('*a')=='previous-good-image');f:close()
            assert(d:writePng(CACHE,bytes))
            f=io.open(CACHE,'rb');assert(f:read('*a')==PNG);f:close()
            assert(not d:writePng(CACHE,'bad image'))
            ''')
        lua.globals().DECODE_FAIL = False

    def test_network_deadline_backoff_and_success_reset(self):
        lua.execute('''
        local d=Plugin:new{language='en',auto_on=true,dash_widget={}}
        d.option=function(_,key,default) if key=='managed_wifi' or key=='wifi_off' then return true end;return default end
        d.record=function() end
        NET.connected=false;NET.on=false;NET.disabled=false
        d.refreshDashboard=function() return true end
        d:requestRefresh(true,false)
        assert(d._busy and UI.queue[d._network_deadline]==60)
        local late=NET.callback;d._network_deadline()
        assert(not d._busy and NET.disabled and UI.queue[d._auto_timer]==60)
        late();assert(d._failures==1)
        NET.connected=true
        d:requestRefresh(true,false)
        assert(d._failures==0 and d._last_error==nil and not d._busy)
        d.refreshDashboard=function() return false end
        d:requestRefresh(true,false);assert(UI.queue[d._auto_timer]==60)
        d:requestRefresh(true,false);assert(UI.queue[d._auto_timer]==120)
        d._failures=99;assert(d:retryDelay()==1800)
        local old=d.dash_widget
        d.buildScreen=function() error('bad render') end
        assert(d:showDashboard('broken',false)==false and d.dash_widget==old)
        ''')

    def test_single_timer_and_wifi_ownership(self):
        lua.execute('''
        for fn in pairs(UI.queue) do UI.queue[fn]=nil end
        local d=Plugin:new{language='en',auto_on=true,dash_widget={}}
        d.record=function() end
        d.refreshDashboard=function() return false end
        NET.connected=true
        d:armAutoRefresh()
        local tick=d._auto_timer;UI.queue[tick]=nil;tick()
        local n=0;for fn in pairs(UI.queue) do n=n+1 end
        assert(n==1 and UI.queue[d._auto_timer]==60, 'duplicate timer or lost backoff')
        d:armAutoRefresh();tick=d._auto_timer;UI.queue[tick]=nil
        d.refreshDashboard=function() return true end;tick()
        n=0;for fn in pairs(UI.queue) do n=n+1 end;assert(n==1)
        d.option=function(_,key,default) if key=='managed_wifi' or key=='wifi_off' then return true end;return default end
        NET.connected=false;NET.on=true;NET.disabled=false
        d:requestRefresh(true,false);d._network_deadline()
        assert(not NET.disabled, 'must preserve existing radio')
        NET.on=false;NET.disabled=false
        d:requestRefresh(true,false);local late=NET.callback
        d:onSuspend();assert(NET.disabled and not d._busy)
        late();assert(d._suspended and not d._busy)
        d._suspended=false;NET.disabled=false
        d:requestRefresh(true,false);NET.callback()
        assert(NET.disabled and not d._busy, 'release owned radio after success')
        NET.connected=true
        ''')
