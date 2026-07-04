--[[
    active_mute.lua — MPV Auto-Mute-Words Plugin
    https://github.com/leviathwaite/MPV-Auto-Mute-Words-Plugin

    Reads a sidecar ".mute.json" timetable that lists time intervals during
    which audio should be muted, then mutes/unmutes MPV automatically as the
    playback position crosses those boundaries while preserving manual mute
    state that was already enabled before an interval began.

    Sidecar file lookup order (first match wins):
      1. <media_dir>/<media_basename_no_ext>.mute.json
      2. <media_dir>/<media_filename>.mute.json   (full name with extension)
      3. <mpv_config_dir>/mute-timetables/<media_basename_no_ext>.mute.json

    JSON timetable format  (see examples/sample.mute.json):
      {
        "version": 1,
        "entries": [
          { "start": 10.5, "end": 11.2 },
          ...
        ]
      }

    User options (set via --script-opts=active_mute-<key>=<value>):
      enabled        = yes     Enable / disable the plugin at startup
      show_osd       = yes     Show OSD messages when muting/unmuting
      osd_duration   = 2000    OSD message duration in milliseconds
      padding        = 0.15    Extra seconds to stay muted before / after each
                               interval (compensates for decode latency)
]]

local mp        = require("mp")
local msg       = require("mp.msg")
local utils     = require("mp.utils")
local options   = require("mp.options")

-- ── User-configurable options ─────────────────────────────────────────────────
local o = {
    enabled      = true,
    show_osd     = true,
    osd_duration = 2000,
    padding      = 0.15,
}
options.read_options(o, "active_mute")

-- ── Module state ──────────────────────────────────────────────────────────────
local mute_intervals  = {}   -- sorted list of {start, end} tables
local plugin_muted    = false
local mute_before_plugin = false
local manual_mute_during_interval = false
local internal_mute_change = false
local timer           = nil
local POLL_INTERVAL   = 0.05 -- seconds between position checks

-- ── Helpers ───────────────────────────────────────────────────────────────────

--- Simple JSON array/object parser (no external dependencies required).
--- Supports only the subset used by the timetable format.
local function json_decode(s)
    -- Delegate to the built-in utils.parse_json when available (mpv ≥ 0.31)
    if utils.parse_json then
        local val, err = utils.parse_json(s)
        if err then
            msg.error("JSON parse error: " .. tostring(err))
        end
        return val
    end
    -- Fallback: very small hand-rolled parser for the timetable subset.
    -- Handles objects, arrays, numbers, strings, booleans, null.
    local pos = 1

    local function skip_ws()
        pos = s:match("^%s*()", pos)
    end

    local parse_value  -- forward declaration

    local function parse_string()
        local res = s:match('^"(.-[^\\])"', pos) or s:match('^""()', pos)
        if res == nil then
            -- empty string edge case
            local empty = s:match('^""()', pos)
            if empty then pos = empty; return "" end
            return nil
        end
        -- Advance past the closing quote
        local _, e = s:find('^".-[^\\]"', pos)
        if not e then
            _, e = s:find('^""', pos)
        end
        pos = (e or pos) + 1
        return res
    end

    local function parse_number()
        local num, np = s:match("^(-?%d+%.?%d*[eE]?[+-]?%d*)()", pos)
        if num then pos = np; return tonumber(num) end
        return nil
    end

    local function parse_array()
        pos = pos + 1  -- skip '['
        local arr = {}
        skip_ws()
        if s:sub(pos, pos) == "]" then pos = pos + 1; return arr end
        while true do
            skip_ws()
            arr[#arr + 1] = parse_value()
            skip_ws()
            local ch = s:sub(pos, pos)
            if ch == "]" then pos = pos + 1; break
            elseif ch == "," then pos = pos + 1
            else break end
        end
        return arr
    end

    local function parse_object()
        pos = pos + 1  -- skip '{'
        local obj = {}
        skip_ws()
        if s:sub(pos, pos) == "}" then pos = pos + 1; return obj end
        while true do
            skip_ws()
            local key = parse_string()
            skip_ws()
            pos = pos + 1  -- skip ':'
            skip_ws()
            obj[key] = parse_value()
            skip_ws()
            local ch = s:sub(pos, pos)
            if ch == "}" then pos = pos + 1; break
            elseif ch == "," then pos = pos + 1
            else break end
        end
        return obj
    end

    parse_value = function()
        skip_ws()
        local ch = s:sub(pos, pos)
        if     ch == '"'  then return parse_string()
        elseif ch == "["  then return parse_array()
        elseif ch == "{"  then return parse_object()
        elseif ch == "t"  then pos = pos + 4; return true
        elseif ch == "f"  then pos = pos + 5; return false
        elseif ch == "n"  then pos = pos + 4; return nil
        else                   return parse_number()
        end
    end

    return parse_value()
end

--- Return base-name without extension and the full filename for a path.
local function split_path(path)
    local dir  = utils.split_path(path)
    local file = path:sub(#dir + 1)
    local base = file:match("^(.+)%.[^%.]+$") or file
    return dir, file, base
end

--- Attempt to read a file; returns its content string or nil.
local function read_file(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local content = f:read("*a")
    f:close()
    return content
end

--- Locate the sidecar .mute.json file for the currently playing media.
local function find_timetable(media_path)
    local dir, file, base = split_path(media_path)
    local config_dir = mp.find_config_file(".")
    if config_dir then
        config_dir = utils.split_path(config_dir)
    else
        config_dir = ""
    end

    local candidates = {
        dir .. base .. ".mute.json",
        dir .. file .. ".mute.json",
        config_dir .. "mute-timetables/" .. base .. ".mute.json",
    }

    for _, candidate in ipairs(candidates) do
        local content = read_file(candidate)
        if content then
            msg.info("Timetable found: " .. candidate)
            return content, candidate
        end
    end
    return nil, nil
end

--- Load and validate the timetable JSON; populate mute_intervals.
local function load_timetable(json_str)
    local data = json_decode(json_str)
    if type(data) ~= "table" then
        msg.error("Timetable root must be a JSON object")
        return false
    end

    local version = data.version or 1
    if version ~= 1 then
        msg.warn("Unknown timetable version " .. tostring(version) ..
                 "; attempting to load anyway")
    end

    local entries = data.entries
    if type(entries) ~= "table" then
        msg.error("Timetable missing 'entries' array")
        return false
    end

    local intervals = {}
    for i, entry in ipairs(entries) do
        local s = tonumber(entry.start or entry["start"])
        local e = tonumber(entry["end"] or entry.stop)
        if s and e and e > s then
            intervals[#intervals + 1] = {
                start = s - o.padding,
                stop  = e + o.padding,
            }
        else
            msg.warn(string.format("Entry %d is invalid (start=%s end=%s); skipped",
                i, tostring(s), tostring(e)))
        end
    end

    -- Sort by start time to allow efficient scanning
    table.sort(intervals, function(a, b) return a.start < b.start end)
    mute_intervals = intervals
    msg.info(string.format("Loaded %d mute interval(s)", #mute_intervals))
    return true
end

-- ── Mute logic ────────────────────────────────────────────────────────────────

local function set_mute_property(mute)
    internal_mute_change = true
    mp.set_property_bool("mute", mute)
    internal_mute_change = false
end

local function set_mute(mute, reason)
    local actual = mp.get_property_bool("mute")
    if plugin_muted == mute and actual == mute then return end

    if mute and not plugin_muted then
        mute_before_plugin = actual == true
        manual_mute_during_interval = false
    end

    plugin_muted = mute
    if actual ~= mute then
        set_mute_property(mute)
    end

    if o.show_osd then
        local label = mute and "🔇 Muted" or "🔊 Unmuted"
        mp.osd_message(label .. " (" .. (reason or "") .. ")", o.osd_duration / 1000)
    end
    msg.verbose((mute and "Muting" or "Unmuting") .. " — " .. (reason or ""))
end

--- Binary search: return the interval that contains `t`, or nil.
local function find_active_interval(t)
    local lo, hi = 1, #mute_intervals
    while lo <= hi do
        local mid = math.floor((lo + hi) / 2)
        local iv  = mute_intervals[mid]
        if t < iv.start then
            hi = mid - 1
        elseif t > iv.stop then
            lo = mid + 1
        else
            return iv
        end
    end
    return nil
end

local function on_tick()
    if not o.enabled or #mute_intervals == 0 then return end
    local t = mp.get_property_number("time-pos")
    if not t then return end

    local iv = find_active_interval(t)
    if iv then
        set_mute(true, string.format("%.2fs–%.2fs", iv.start, iv.stop))
    else
        if plugin_muted then
            local keep_muted = mute_before_plugin or manual_mute_during_interval
            plugin_muted = false
            mute_before_plugin = false
            manual_mute_during_interval = false
            if keep_muted then
                msg.verbose("Leaving mute enabled due to manual mute precedence")
            else
                set_mute(false, "interval ended")
            end
        end
    end
end

-- ── Event handlers ────────────────────────────────────────────────────────────

local function stop_timer()
    if timer then
        timer:kill()
        timer = nil
    end
end

local function on_file_loaded()
    -- Reset state
    stop_timer()
    mute_intervals = {}
    mute_before_plugin = false
    manual_mute_during_interval = false
    if plugin_muted then
        set_mute_property(false)
        plugin_muted = false
    end

    if not o.enabled then
        msg.info("Plugin disabled via options")
        return
    end

    local path = mp.get_property("path")
    if not path then return end

    local json_str, timetable_path = find_timetable(path)
    if not json_str then
        msg.info("No sidecar timetable found for: " .. path)
        return
    end

    if not load_timetable(json_str) then
        msg.error("Failed to load timetable: " .. (timetable_path or "?"))
        return
    end

    -- Start polling the playback position
    timer = mp.add_periodic_timer(POLL_INTERVAL, on_tick)
end

local function on_end_file()
    stop_timer()
    if plugin_muted then
        set_mute_property(false)
        plugin_muted = false
    end
    mute_before_plugin = false
    manual_mute_during_interval = false
    mute_intervals = {}
end

mp.observe_property("mute", "bool", function(_, value)
    if internal_mute_change then
        return
    end
    if plugin_muted and value == true then
        manual_mute_during_interval = true
    end
end)

-- ── Keybinding / script-message handlers ─────────────────────────────────────

mp.register_script_message("active-mute-toggle", function()
    o.enabled = not o.enabled
    local state = o.enabled and "enabled" or "disabled"
    mp.osd_message("Auto-mute " .. state, o.osd_duration / 1000)
    msg.info("Plugin " .. state)
    if not o.enabled and plugin_muted then
        set_mute_property(false)
        plugin_muted = false
        mute_before_plugin = false
        manual_mute_during_interval = false
    end
end)

mp.register_script_message("active-mute-reload", function()
    on_file_loaded()
    mp.osd_message("Mute timetable reloaded", o.osd_duration / 1000)
end)

-- ── Wire up events ────────────────────────────────────────────────────────────
mp.register_event("file-loaded", on_file_loaded)
mp.register_event("end-file",    on_end_file)
